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
    const filename = `voice_${timestamp}.ogg`;
    const filepath = path.join(DOWNLOAD_DIR, filename);

    fs.writeFileSync(filepath, buffer);

    return filepath;
}

module.exports = { downloadMedia };
