import makeWASocket, { useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } from '@whiskeysockets/baileys';
import QRCode from 'qrcode-terminal';
import axios from 'axios';
import path from 'path';
import fs from 'fs';
import express from 'express';
import { downloadMedia } from './downloader.js';
import { sendText, sendAudio, sendButtons } from './sender.js';

import { fileURLToPath } from 'url';
import { createRequire } from 'module';
const require = createRequire(import.meta.url);
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const FASTAPI_URL = process.env.FASTAPI_URL || 'http://localhost:8000';
const AUTH_FOLDER = process.env.WHATSAPP_AUTH_FOLDER || path.join(__dirname, 'auth_info');
const KEEPALIVE_PHONE = process.env.KEEPALIVE_PHONE || '919871208803';
const KEEPALIVE_INTERVAL_MS = parseInt(process.env.KEEPALIVE_INTERVAL_MS) || 15 * 60 * 1000;
const MAX_DECRYPTION_FAILS = 5;
const DECRYPTION_FAIL_WINDOW_MS = 60000;

let currentSock = null;
let isReconnecting = false;
let latestQr = null;
let connectionStatus = 'starting';
let decryptionFails = [];
let decryptionResetting = false;
let keepaliveTimer = null;
let pairingPhone = null;
let latestPairingCode = null;

const messageStore = new Map();

function storeMessage(key, msg) {
    const id = key?.id;
    if (id) messageStore.set(id, msg);
}

async function getMessage(key) {
    const msg = messageStore.get(key?.id);
    return msg || undefined;
}

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

function cleanupOldBackups() {
    try {
        const backupDirs = fs.readdirSync(path.join(__dirname))
            .filter(d => d.startsWith('auth_info_backup_'))
            .sort()
            .reverse();
        for (const dir of backupDirs.slice(3)) {
            const dirPath = path.join(__dirname, dir);
            fs.rmSync(dirPath, { recursive: true, force: true });
            console.log(`[whatsapp] Cleaned up old backup: ${dir}`);
        }
    } catch (err) {
        console.error('[whatsapp] Backup cleanup error:', err.message);
    }
}

function startKeepalive() {
    stopKeepalive();
    if (!KEEPALIVE_PHONE) return;
    console.log(`[keepalive] Will ping ${KEEPALIVE_PHONE} every ${KEEPALIVE_INTERVAL_MS / 1000}s`);
    keepaliveTimer = setInterval(async () => {
        if (!currentSock || connectionStatus !== 'open') return;
        try {
            const jid = KEEPALIVE_PHONE.includes('@') ? KEEPALIVE_PHONE : `${KEEPALIVE_PHONE}@s.whatsapp.net`;
            await currentSock.sendPresenceUpdate('available', jid);
            console.log(`[keepalive] Presence ping sent at ${new Date().toLocaleTimeString()}`);
        } catch (err) {
            console.error('[keepalive] Ping failed:', err.message);
            if (err.message && (err.message.includes('Not connected') || err.message.includes('Connection closed'))) {
                console.log('[keepalive] Connection lost, attempting reconnect...');
                stopKeepalive();
                isReconnecting = false;
                setTimeout(() => connectWhatsApp(), 3000);
            }
        }
    }, KEEPALIVE_INTERVAL_MS);

    setInterval(async () => {
        if (!currentSock || connectionStatus !== 'open') return;
        try {
            const jid = KEEPALIVE_PHONE.includes('@') ? KEEPALIVE_PHONE : `${KEEPALIVE_PHONE}@s.whatsapp.net`;
            await currentSock.sendPresenceUpdate('available', jid);
        } catch (err) {}
    }, 3 * 60 * 1000);
}

function stopKeepalive() {
    if (keepaliveTimer) {
        clearInterval(keepaliveTimer);
        keepaliveTimer = null;
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

function extractSenderJid(msg) {
    let senderId = msg.key?.remoteJid;
    if (senderId && senderId.endsWith('@lid')) {
        if (msg.key?.senderPn) {
            senderId = msg.key.senderPn;
        } else if (msg.key?.remoteJidAlt) {
            senderId = msg.key.remoteJidAlt;
        } else if (msg.messageStubParameters && msg.messageStubParameters[0]) {
            senderId = msg.messageStubParameters[0];
        }
    }
    return senderId;
}

async function connectWhatsApp() {
    if (isReconnecting) return;
    isReconnecting = true;

    try {
        const { version } = await fetchLatestBaileysVersion();
        console.log(`Using WA web version: ${version.join('.')}`);

        const { state, saveCreds } = await useMultiFileAuthState(AUTH_FOLDER);

        const usePairing = Boolean(pairingPhone);

        const sock = makeWASocket({
            version,
            auth: state,
            logger: createBaileysLogger(),
            browser: usePairing ? ['Chrome (Linux)', '', ''] : ['Clinic Agent', 'Chrome', '1.0.0'],
            printQRInTerminal: false,
            getMessage,
        });

        currentSock = sock;

        sock.ev.on('creds.update', saveCreds);

        sock.ev.on('connection.update', async (update) => {
            const { connection, lastDisconnect, qr } = update;

            if (qr && !usePairing) {
                latestQr = qr;
                connectionStatus = 'qr';
                console.log('\nScan this QR code with your clinic WhatsApp:\n');
                QRCode.generate(qr, { small: true });
            }

            if (connection === 'connecting' && usePairing && !state.creds.registered) {
                try {
                    const phoneDigits = pairingPhone.replace(/[^0-9]/g, '');
                    const code = await sock.requestPairingCode(phoneDigits);
                    latestPairingCode = code;
                    connectionStatus = 'pairing';
                    console.log(`\nPairing Code: ${code}`);
                    console.log(`Enter this code on your phone: WhatsApp > Linked Devices > Link with phone number\n`);
                } catch (err) {
                    console.error('[whatsapp] Failed to request pairing code:', err.message);
                    pairingPhone = null;
                    latestPairingCode = null;
                }
            }

            if (connection === 'close') {
                currentSock = null;
                connectionStatus = 'closed';
                stopKeepalive();
                const statusCode = getStatusCode(lastDisconnect?.error);
                const loggedOut = statusCode === DisconnectReason.loggedOut;
                const badSession = statusCode === DisconnectReason.badSession;
                const restartRequired = statusCode === DisconnectReason.restartRequired;
                console.log('Connection closed. Logged out:', loggedOut, '| Status:', statusCode);

                if (loggedOut) {
                    console.log('Logged out. Use Pairing Code or delete auth_info and restart to scan QR again.');
                } else if (badSession || restartRequired) {
                    console.log(`Session error (${statusCode}). Resetting auth and reconnecting...`);
                    deleteAuthAndReconnect(`Session error: ${statusCode}`);
                } else {
                    console.log('Reconnecting in 5 seconds...');
                    isReconnecting = false;
                    setTimeout(() => connectWhatsApp(), 5000);
                }
            }

            if (connection === 'open') {
                isReconnecting = false;
                latestQr = null;
                latestPairingCode = null;
                connectionStatus = 'open';
                decryptionFails = [];
                startKeepalive();
                cleanupOldBackups();
                console.log('WhatsApp bot connected!');
            }
        });

        sock.ev.on('messages.upsert', async ({ messages, type }) => {
            const msg = messages[0];
            const rawJid = msg.key?.remoteJid;
            const senderJid = extractSenderJid(msg);
            console.log(`[whatsapp] Message upsert: type=${type}, fromMe=${msg.key?.fromMe}, remoteJid=${rawJid}, resolvedJid=${senderJid}, hasMessage=${!!msg.message}`);

            if (!msg.message) return;

            storeMessage(msg.key, msg);

            if (msg.key.fromMe) {
                let staffText = '';
                if (msg.message.conversation) {
                    staffText = msg.message.conversation;
                } else if (msg.message.extendedTextMessage) {
                    staffText = msg.message.extendedTextMessage.text;
                }
                if (staffText && senderJid && !senderJid.endsWith('@g.us')) {
                    const staffPhone = senderJid.split('@')[0];
                    try {
                        await axios.post(`${FASTAPI_URL}/webhook/staff-message`, {
                            phone: staffPhone,
                            message_text: staffText,
                        }, { timeout: 5000 });
                        console.log(`[whatsapp] Staff message tracked for ${staffPhone}: "${staffText.substring(0, 50)}"`);
                    } catch (err) {
                        console.error('[whatsapp] Failed to track staff message:', err.message);
                    }
                }
                return;
            }

            if (type !== 'notify') {
                console.log(`[whatsapp] Ignoring non-notify message type: ${type}`);
                return;
            }

            if (senderJid.endsWith('@g.us') || senderJid.endsWith('@newsletter') || senderJid.endsWith('@broadcast')) {
                console.log(`[whatsapp] Ignoring group/newsletter/broadcast: ${senderJid}`);
                return;
            }

            const IGNORE_MESSAGE_TYPES = ['protocolMessage', 'reactionMessage', 'stickerMessage', 'contactMessage', 'contactsArrayMessage', 'locationMessage', 'liveLocationMessage'];
            const msgKeys = Object.keys(msg.message);
            const isOnlyIgnored = msgKeys.every(k => IGNORE_MESSAGE_TYPES.includes(k));
            if (isOnlyIgnored) {
                console.log(`[whatsapp] Ignoring non-content message types: ${msgKeys.join(', ')}`);
                return;
            }

            const phone = senderJid.split('@')[0];

            let messageText = '';
            let audioPath = null;
            let imagePath = null;

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

            if (msg.message.imageMessage) {
                if (!messageText && msg.message.imageMessage.caption) {
                    messageText = msg.message.imageMessage.caption;
                }
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

            const imageMsg = msg.message.imageMessage;
            if (imageMsg) {
                try {
                    imagePath = await downloadMedia(sock, msg);
                    console.log(`Image downloaded: ${imagePath}`);
                } catch (err) {
                    console.error('Failed to download image:', err);
                }
            }

            const docMsg = msg.message.documentMessage;
            if (docMsg) {
                try {
                    const docPath = await downloadMedia(sock, msg);
                    console.log(`Document downloaded: ${docPath}`);
                    imagePath = docPath;
                    if (!messageText) {
                        messageText = docMsg.caption || `[Patient sent a document: ${docMsg.fileName || 'file'}]`;
                    }
                } catch (err) {
                    console.error('Failed to download document:', err);
                }
            }

            const videoMsg = msg.message.videoMessage;
            if (videoMsg) {
                if (!messageText && videoMsg.caption) {
                    messageText = videoMsg.caption;
                } else if (!messageText) {
                    messageText = '[Patient sent a video]';
                }
            }

            if (!messageText && !audioPath && !imagePath) {
                console.log(`[whatsapp] No text/audio/image extracted from message. Message keys: ${Object.keys(msg.message || {}).join(', ')}`);
                return;
            }

            console.log(`[whatsapp] Processing message from ${phone}: "${messageText}"`);

            try {
                await sock.sendPresenceUpdate('composing', senderJid);
            } catch (e) { }

            try {
                const isButtonReply = !audioPath && /^\d$/.test(messageText.trim());

                let response;
                const maxRetries = 3;
                let lastErr = null;
                for (let attempt = 1; attempt <= maxRetries; attempt++) {
                    try {
                        if (isButtonReply) {
                            response = await axios.post(`${FASTAPI_URL}/webhook/button-reply`, {
                                phone: phone,
                                button_number: parseInt(messageText.trim()),
                            }, { timeout: 30000 });
                        } else {
                            const reqTimeout = 600000;
                            response = await axios.post(`${FASTAPI_URL}/webhook/message`, {
                                phone: phone,
                                message_text: messageText || null,
                                audio_path: audioPath,
                                image_path: imagePath,
                            }, { timeout: reqTimeout });
                        }
                        lastErr = null;
                        break;
                    } catch (retryErr) {
                        lastErr = retryErr;
                        const isConnErr = retryErr.code === 'ECONNREFUSED' || retryErr.code === 'ETIMEDOUT' || retryErr.code === 'ECONNRESET' || retryErr.code === 'ECONNABORTED';
                        if (isConnErr && attempt < maxRetries) {
                            console.log(`[whatsapp] Retry ${attempt}/${maxRetries} for ${phone}...`);
                            await new Promise(r => setTimeout(r, 2000 * attempt));
                            continue;
                        }
                        throw retryErr;
                    }
                }

                if (lastErr) throw lastErr;

                if (isButtonReply) {
                    const reply = response.data.reply;
                    if (reply) {
                        await sock.sendPresenceUpdate('paused', senderJid).catch(() => {});
                        await sendText(sock, senderJid, reply);
                    }
                } else {
                    const { text_reply, audio_path: reply_audio } = response.data;
                    console.log(`Backend response: text_reply="${text_reply}", audio_path="${reply_audio}"`);

                    let textSent = false;
                    if (text_reply) {
                        console.log(`Attempting to send text to ${senderJid}...`);
                        await sock.sendPresenceUpdate('paused', senderJid).catch(() => {});
                        await sendText(sock, senderJid, text_reply);
                        console.log(`Successfully sent text to ${senderJid}`);
                        textSent = true;
                    }

                    if (reply_audio) {
                        try {
                            console.log(`Attempting to send audio to ${senderJid}...`);
                            await sock.sendPresenceUpdate('composing', senderJid).catch(() => {});
                            await sendAudio(sock, senderJid, reply_audio);
                            console.log(`Successfully sent audio to ${senderJid}`);
                        } catch (audioErr) {
                            console.error(`Failed to send audio to ${senderJid}:`, audioErr.message);
                        }
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
                    await sock.sendPresenceUpdate('paused', senderJid).catch(() => {});
                    await sendText(sock, senderJid, "Please allow me some time, I will get back to you shortly.");
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
        pairing_code: latestPairingCode,
        auth_folder: AUTH_FOLDER,
        decryption_fails: decryptionFails.length,
    });
});

app.post('/send/text', async (req, res) => {
    try {
        const { phone, text } = req.body;
        if (!currentSock) return res.status(503).json({ error: 'WhatsApp not connected' });
        const jid = phone.includes('@') ? phone : `${phone}@s.whatsapp.net`;
        try { await currentSock.sendPresenceUpdate('composing', jid); } catch (e) {}
        await new Promise(r => setTimeout(r, 800));
        await sendText(currentSock, jid, text);
        try { await currentSock.sendPresenceUpdate('paused', jid); } catch (e) {}
        res.json({ success: true });
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});

app.post('/send/audio', async (req, res) => {
    try {
        const { phone, audio_path } = req.body;
        if (!currentSock) return res.status(503).json({ error: 'WhatsApp not connected' });
        const jid = phone.includes('@') ? phone : `${phone}@s.whatsapp.net`;
        try { await currentSock.sendPresenceUpdate('composing', jid); } catch (e) {}
        await new Promise(r => setTimeout(r, 500));
        await sendAudio(currentSock, jid, audio_path);
        try { await currentSock.sendPresenceUpdate('paused', jid); } catch (e) {}
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

app.post('/pairing-code', async (req, res) => {
    try {
        const { phone } = req.body;
        if (!phone) return res.status(400).json({ error: 'Phone number required (e.g. 919871208803)' });

        const phoneDigits = phone.replace(/[^0-9]/g, '');
        if (phoneDigits.length < 10) return res.status(400).json({ error: 'Invalid phone number' });

        pairingPhone = phoneDigits;
        latestPairingCode = null;
        latestQr = null;

        if (currentSock) {
            try { currentSock.end(undefined); } catch (e) {}
            currentSock = null;
        }
        stopKeepalive();

        isReconnecting = false;
        connectWhatsApp();

        const deadline = Date.now() + 15000;
        while (!latestPairingCode && Date.now() < deadline) {
            await new Promise(r => setTimeout(r, 500));
        }

        if (latestPairingCode) {
            res.json({ success: true, pairing_code: latestPairingCode, phone: phoneDigits });
        } else {
            res.json({ success: false, error: 'Pairing code not generated yet. Check the log.' });
        }
    } catch (err) {
        res.status(500).json({ error: err.message });
    }
});

const PORT = process.env.BOT_PORT || 3001;
app.listen(PORT, () => {
    console.log(`Bot HTTP server running on port ${PORT}`);
});

connectWhatsApp().catch(console.error);
