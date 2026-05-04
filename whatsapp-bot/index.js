const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require('@whiskeysockets/baileys');
const QRCode = require('qrcode-terminal');
const axios = require('axios');
const path = require('path');
const fs = require('fs');
const express = require('express');
const { downloadMedia } = require('./downloader');
const { sendText, sendAudio, sendButtons } = require('./sender');

const FASTAPI_URL = process.env.FASTAPI_URL || 'http://localhost:8000';
const AUTH_FOLDER = process.env.WHATSAPP_AUTH_FOLDER || path.join(__dirname, 'auth_info');
const MAX_DECRYPTION_FAILS = 5;
const DECRYPTION_FAIL_WINDOW_MS = 60000;

let currentSock = null;
let isReconnecting = false;
let latestQr = null;
let connectionStatus = 'starting';
let decryptionFails = [];
let decryptionResetting = false;

function getStatusCode(error) {
    if (!error) return null;
    if (error?.output?.statusCode) return error.output.statusCode;
    const data = error?.data || error?.data?.data;
    if (data?.statusCode) return data.statusCode;
    return null;
}

function recordDecryptionFail() {
    const now = Date.now();
    decryptionFails.push(now);
    decryptionFails = decryptionFails.filter(t => now - t < DECRYPTION_FAIL_WINDOW_MS);
    console.log(`[whatsapp] Decryption failure (${decryptionFails.length}/${MAX_DECRYPTION_FAILS})`);

    if (decryptionFails.length >= MAX_DECRYPTION_FAILS) {
        deleteAuthAndReconnect(`${MAX_DECRYPTION_FAILS} decryption errors in ${DECRYPTION_FAIL_WINDOW_MS / 1000}s`);
    }
}

async function deleteAuthAndReconnect(reason) {
    if (decryptionResetting) return;
    decryptionResetting = true;
    currentSock = null;
    connectionStatus = 'resetting';
    console.log(`[whatsapp] Resetting auth session: ${reason}`);

    try {
        if (fs.existsSync(AUTH_FOLDER)) {
            fs.rmSync(AUTH_FOLDER, { recursive: true, force: true });
            console.log('[whatsapp] Deleted auth_info folder');
        }
    } catch (err) {
        console.error('[whatsapp] Failed to delete auth folder:', err.message);
    }

    decryptionFails = [];
    decryptionResetting = false;
    isReconnecting = false;
    console.log('[whatsapp] Reconnecting with fresh session in 3 seconds...');
    setTimeout(() => connectWhatsApp(), 3000);
}

function createBaileysLogger() {
    const pino = require('pino');
    const logger = pino({ level: 'silent' });

    const origError = logger.error.bind(logger);
    logger.error = function (...args) {
        const msg = args.map(a => typeof a === 'string' ? a : a?.message || a?.msg || '').join(' ');
        if (msg.includes('Bad MAC') || msg.includes('MessageCounterError') || msg.includes('Failed to decrypt')) {
            recordDecryptionFail();
        }
        return origError(...args);
    };

    const origWarn = logger.warn.bind(logger);
    logger.warn = function (...args) {
        const msg = args.map(a => typeof a === 'string' ? a : a?.message || a?.msg || '').join(' ');
        if (msg.includes('Bad MAC') || msg.includes('MessageCounterError') || msg.includes('Failed to decrypt')) {
            recordDecryptionFail();
        }
        return origWarn(...args);
    };

    return logger;
}

async function connectWhatsApp() {
    if (isReconnecting) return;
    isReconnecting = true;

    try {
        const { version } = await fetchLatestBaileysVersion();
        console.log(`Using WA web version: ${version.join('.')}`);

        const { state, saveCreds } = await useMultiFileAuthState(AUTH_FOLDER);

        const sock = makeWASocket({
            version,
            auth: state,
            logger: createBaileysLogger(),
            browser: ['Clinic Agent', 'Chrome', '1.0.0'],
            printQRInTerminal: false,
        });

        currentSock = sock;

        sock.ev.on('creds.update', saveCreds);

        sock.ev.on('connection.update', async (update) => {
            const { connection, lastDisconnect, qr } = update;

            if (qr) {
                latestQr = qr;
                connectionStatus = 'qr';
                console.log('\nScan this QR code with your clinic WhatsApp:\n');
                QRCode.generate(qr, { small: true });
            }

            if (connection === 'close') {
                currentSock = null;
                connectionStatus = 'closed';
                const statusCode = getStatusCode(lastDisconnect?.error);
                const loggedOut = statusCode === DisconnectReason.loggedOut;
                console.log('Connection closed. Logged out:', loggedOut, '| Status:', statusCode);
                if (!loggedOut) {
                    console.log('Reconnecting in 5 seconds...');
                    isReconnecting = false;
                    setTimeout(() => connectWhatsApp(), 5000);
                } else {
                    console.log('Logged out. Delete auth_info folder and restart to scan QR again.');
                }
            }

            if (connection === 'open') {
                isReconnecting = false;
                latestQr = null;
                connectionStatus = 'open';
                decryptionFails = [];
                console.log('WhatsApp bot connected!');
            }
        });

        sock.ev.on('messages.upsert', async ({ messages }) => {
            const msg = messages[0];
            if (!msg.key || msg.key.fromMe) return;
            if (!msg.message) return;

            if (msg.key.remoteJid.endsWith('@g.us')) return;

            const senderJid = msg.key.remoteJid;
            const phone = senderJid.split('@')[0];

            let messageText = '';
            let audioPath = null;

            if (msg.message.conversation) {
                messageText = msg.message.conversation;
            } else if (msg.message.extendedTextMessage) {
                messageText = msg.message.extendedTextMessage.text;
            } else if (msg.message.buttonsResponseMessage) {
                const selectedId = msg.message.buttonsResponseMessage.selectedDisplayText;
                const buttonMap = { 'Reschedule': '1', 'Found doctor': '2', 'Call back later': '3', 'Unwell - need help': '4' };
                messageText = buttonMap[selectedId] || selectedId;
            } else if (msg.message.listResponseMessage) {
                messageText = msg.message.listResponseMessage.singleSelectReply.selectedRowId;
            }

            const audioMsg = msg.message.audioMessage || msg.message.voiceNoteMessage?.audioMessage;
            if (audioMsg) {
                try {
                    audioPath = await downloadMedia(sock, msg);
                    console.log(`Audio downloaded: ${audioPath}`);
                } catch (err) {
                    console.error('Failed to download audio:', err);
                }
            }

            if (!messageText && !audioPath) return;

            try {
                const isButtonReply = !audioPath && /^\d$/.test(messageText.trim());

                let response;
                if (isButtonReply) {
                    response = await axios.post(`${FASTAPI_URL}/webhook/button-reply`, {
                        phone: phone,
                        button_number: parseInt(messageText.trim()),
                    });
                    const reply = response.data.reply;
                    if (reply) {
                        await sendText(sock, senderJid, reply);
                    }
                } else {
                    response = await axios.post(`${FASTAPI_URL}/webhook/message`, {
                        phone: phone,
                        message_text: messageText || null,
                        audio_path: audioPath,
                    });

                    const { text_reply, audio_path: reply_audio } = response.data;
                    console.log(`Backend response: text_reply="${text_reply}", audio_path="${reply_audio}"`);

                    if (text_reply) {
                        console.log(`Attempting to send text to ${senderJid}...`);
                        await sendText(sock, senderJid, text_reply);
                        console.log(`Successfully sent text to ${senderJid}`);
                    }

                    if (reply_audio) {
                        console.log(`Attempting to send audio to ${senderJid}...`);
                        await sendAudio(sock, senderJid, reply_audio);
                        console.log(`Successfully sent audio to ${senderJid}`);
                    }
                }
            } catch (err) {
                const errDetail = err.response
                    ? `HTTP ${err.response.status}: ${JSON.stringify(err.response.data)}`
                    : err.code
                        ? `${err.code} - ${err.message || 'Connection failed'}`
                        : err.message || String(err);
                console.error('Error processing message:', errDetail);
                try {
                    await sendText(sock, senderJid, "I'm having trouble right now. Please try again in a moment.");
                } catch (sendErr) {
                    console.error('Failed to send error message:', sendErr.message);
                }
            }
        });

    } catch (err) {
        console.error('Failed to connect:', err.message);
        isReconnecting = false;
        console.log('Retrying in 10 seconds...');
        setTimeout(() => connectWhatsApp(), 10000);
    }
}

process.on('unhandledRejection', (reason) => {
    const str = String(reason);
    if (str.includes('Bad MAC') || str.includes('MessageCounterError')) {
        recordDecryptionFail();
    }
});

const app = express();
app.use(express.json());

app.get('/status', (req, res) => {
    res.json({
        connected: Boolean(currentSock && connectionStatus === 'open'),
        status: connectionStatus,
        qr: latestQr,
        auth_folder: AUTH_FOLDER,
        decryption_fails: decryptionFails.length,
    });
});

app.post('/send/text', async (req, res) => {
    try {
        const { phone, text } = req.body;
        if (!currentSock) return res.status(503).json({ error: 'WhatsApp not connected' });
        await sendText(currentSock, phone, text);
        res.json({ success: true });
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});

app.post('/send/audio', async (req, res) => {
    try {
        const { phone, audio_path } = req.body;
        if (!currentSock) return res.status(503).json({ error: 'WhatsApp not connected' });
        await sendAudio(currentSock, phone, audio_path);
        res.json({ success: true });
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});

app.post('/send/buttons', async (req, res) => {
    try {
        const { phone, text, buttons } = req.body;
        if (!currentSock) return res.status(503).json({ error: 'WhatsApp not connected' });
        await sendButtons(currentSock, phone, text, buttons);
        res.json({ success: true });
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});

app.post('/reset-session', async (req, res) => {
    await deleteAuthAndReconnect('Manual reset via API');
    res.json({ message: 'Session reset initiated', status: connectionStatus });
});

const PORT = process.env.BOT_PORT || 3001;
app.listen(PORT, () => {
    console.log(`Bot HTTP server running on port ${PORT}`);
});

connectWhatsApp().catch(console.error);
