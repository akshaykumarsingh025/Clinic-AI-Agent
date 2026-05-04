function toJid(phone) {
    if (phone.includes('@')) return phone;
    if (phone.length > 15 && !phone.startsWith('+')) return `${phone}@lid`;
    return `${phone}@s.whatsapp.net`;
}

async function sendText(sock, phone, text) {
    await sock.sendMessage(toJid(phone), { text: text });
}

async function sendAudio(sock, phone, audioPath) {
    const fs = require('fs');

    if (!fs.existsSync(audioPath)) {
        throw new Error(`Audio file not found: ${audioPath}`);
    }

    const audioBuffer = fs.readFileSync(audioPath);

    await sock.sendMessage(toJid(phone), {
        audio: audioBuffer,
        mimetype: 'audio/wav',
        ptt: true,
    });
}

async function sendButtons(sock, phone, text, buttons) {
    const buttonParams = buttons.map((label, index) => ({
        buttonId: `btn_${index + 1}`,
        buttonText: { displayText: label },
        type: 1,
    }));

    const buttonMessage = {
        text: text,
        buttons: buttonParams,
        headerType: 1,
    };

    await sock.sendMessage(toJid(phone), buttonMessage);
}

module.exports = { sendText, sendAudio, sendButtons };
