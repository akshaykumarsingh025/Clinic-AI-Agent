async function sendText(sock, phone, text) {
    const jid = phone.includes('@') ? phone : `${phone}@s.whatsapp.net`;
    await sock.sendMessage(jid, { text: text });
}

async function sendAudio(sock, phone, audioPath) {
    const jid = phone.includes('@') ? phone : `${phone}@s.whatsapp.net`;
    const fs = require('fs');
    const path = require('path');

    if (!fs.existsSync(audioPath)) {
        throw new Error(`Audio file not found: ${audioPath}`);
    }

    const audioBuffer = fs.readFileSync(audioPath);

    await sock.sendMessage(jid, {
        audio: audioBuffer,
        mimetype: 'audio/wav',
        ptt: true,
    });
}

async function sendButtons(sock, phone, text, buttons) {
    const jid = phone.includes('@') ? phone : `${phone}@s.whatsapp.net`;

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

    await sock.sendMessage(jid, buttonMessage);
}

module.exports = { sendText, sendAudio, sendButtons };
