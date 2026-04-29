const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require('@whiskeysockets/baileys');
const QRCode = require('qrcode-terminal');
const axios = require('axios');
const path = require('path');
const express = require('express');
const { downloadMedia } = require('./downloader');
const { sendText, sendAudio, sendButtons } = require('./sender');

const FASTAPI_URL = process.env.FASTAPI_URL || 'http://localhost:8000';
const AUTH_FOLDER = path.join(__dirname, 'auth_info');

let currentSock = null;
let isReconnecting = false;

function getStatusCode(error) {
    if (!error) return null;
    if (error?.output?.statusCode) return error.output.statusCode;
    const data = error?.data || error?.data?.data;
    if (data?.statusCode) return data.statusCode;
    return null;
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
            logger: require('pino')({ level: 'silent' }),
            browser: ['Clinic Agent', 'Chrome', '1.0.0'],
        });

        currentSock = sock;

        sock.ev.on('creds.update', saveCreds);

        sock.ev.on('connection.update', async (update) => {
            const { connection, lastDisconnect, qr } = update;

            if (qr) {
                console.log('\n📱 Scan this QR code with your clinic WhatsApp:\n');
                QRCode.generate(qr, { small: true });
            }

            if (connection === 'close') {
                currentSock = null;
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
                console.log('✅ WhatsApp bot connected!');
            }
        });

        sock.ev.on('messages.upsert', async ({ messages }) => {
            const msg = messages[0];
            if (!msg.key || msg.key.fromMe) return;
            if (!msg.message) return;

            if (msg.key.remoteJid.endsWith('@g.us')) return;

            const phone = msg.key.remoteJid.replace('@s.whatsapp.net', '');

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
                        await sendText(sock, phone, reply);
                    }
                } else {
                    response = await axios.post(`${FASTAPI_URL}/webhook/message`, {
                        phone: phone,
                        message_text: messageText || null,
                        audio_path: audioPath,
                    });

                    const { text_reply, audio_path: reply_audio } = response.data;

                    if (text_reply) {
                        await sendText(sock, phone, text_reply);
                    }

                    if (reply_audio) {
                        await sendAudio(sock, phone, reply_audio);
                    }
                }
            } catch (err) {
                console.error('Error processing message:', err.message);
                try {
                    await sendText(sock, phone, "I'm having trouble right now. Please try again in a moment.");
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

const app = express();
app.use(express.json());

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

const PORT = process.env.BOT_PORT || 3001;
app.listen(PORT, () => {
    console.log(`Bot HTTP server running on port ${PORT}`);
});

connectWhatsApp().catch(console.error);
