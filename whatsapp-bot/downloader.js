const fs = require('fs');
const path = require('path');
const { downloadMediaMessage } = require('@whiskeysockets/baileys');

const DOWNLOAD_DIR = path.join(__dirname, '..', 'audio_cache', 'incoming');

if (!fs.existsSync(DOWNLOAD_DIR)) {
    fs.mkdirSync(DOWNLOAD_DIR, { recursive: true });
}

async function downloadMedia(sock, msg) {
    let buffer;

    try {
        buffer = await downloadMediaMessage(msg, 'buffer', {}, {
            reuploadRequest: sock.updateMediaMessage,
        });
    } catch (err) {
        try {
            buffer = await downloadMediaMessage(msg, 'buffer', {});
        } catch (err2) {
            throw new Error(`Failed to download media: ${err2.message}`);
        }
    }

    if (!buffer || buffer.length === 0) {
        throw new Error('Downloaded media buffer is empty');
    }

    const timestamp = Date.now();
    let filename;
    let mimetype = '';

    const imageMsg = msg.message?.imageMessage;
    const audioMsg = msg.message?.audioMessage || msg.message?.voiceNoteMessage?.audioMessage;

    if (imageMsg) {
        mimetype = imageMsg.mimetype || 'image/jpeg';
        const ext = mimetype.split('/')[1] || 'jpeg';
        filename = `id_${timestamp}.${ext}`;
    } else if (audioMsg) {
        mimetype = audioMsg.mimetype || 'audio/ogg';
        filename = `voice_${timestamp}.ogg`;
    } else {
        filename = `media_${timestamp}`;
    }

    const subdir = imageMsg ? 'id_cards' : 'incoming';
    const dir = path.join(DOWNLOAD_DIR, '..', subdir);
    if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
    }

    const filepath = path.join(dir, filename);
    fs.writeFileSync(filepath, buffer);

    return filepath;
}

module.exports = { downloadMedia };
