import fs from 'fs';
import path from 'path';
import { execFile } from 'child_process';
import { promisify } from 'util';
const execFileAsync = promisify(execFile);

function toJid(phone) {
    if (phone.includes('@')) return phone;
    return `${phone}@s.whatsapp.net`;
}

async function sendText(sock, phone, text) {
    await sock.sendMessage(toJid(phone), { text: text });
}

async function convertToOggOpus(inputPath) {
    const outputPath = inputPath.replace(/\.[^.]+$/, '') + '_wa.ogg';

    let ffmpegPath;
    try {
        ffmpegPath = (await import('ffmpeg-static')).default;
    } catch (e) {
        ffmpegPath = 'ffmpeg';
    }

    const ffmpegArgs = [
        '-y', '-i', inputPath,
        '-c:a', 'libopus',
        '-b:a', '64k',
        '-ar', '48000',
        '-ac', '1',
    ];

    try {
        await execFileAsync(ffmpegPath, [...ffmpegArgs, '-application', 'voip', outputPath], { timeout: 30000 });
        if (fs.existsSync(outputPath)) return outputPath;
    } catch (err) {
        console.warn('[sender] ffmpeg with -application voip failed, retrying without it:', err.message);
    }

    try {
        await execFileAsync(ffmpegPath, [...ffmpegArgs, outputPath], { timeout: 30000 });
        if (fs.existsSync(outputPath)) return outputPath;
    } catch (err) {
        console.error('[sender] ffmpeg conversion failed:', err.message);
    }
    return null;
}

async function sendAudio(sock, phone, audioPath) {
    if (!fs.existsSync(audioPath)) {
        throw new Error(`Audio file not found: ${audioPath}`);
    }

    let sendPath = audioPath;
    const ext = path.extname(audioPath).toLowerCase();

    if (ext !== '.ogg' && ext !== '.opus') {
        console.log(`[sender] Converting ${ext} to OGG Opus for WhatsApp voice note...`);
        const oggPath = await convertToOggOpus(audioPath);
        if (oggPath) {
            sendPath = oggPath;
            console.log(`[sender] Converted to OGG: ${oggPath}`);
        } else {
            console.warn(`[sender] OGG conversion failed, sending as regular audio (not PTT)`);
            const audioBuffer = fs.readFileSync(audioPath);
            const mimeMap = { '.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4' };
            await sock.sendMessage(toJid(phone), {
                audio: audioBuffer,
                mimetype: mimeMap[ext] || 'audio/wav',
                ptt: false,
            });
            return;
        }
    }

    const audioBuffer = fs.readFileSync(sendPath);

    await sock.sendMessage(toJid(phone), {
        audio: audioBuffer,
        mimetype: 'audio/ogg; codecs=opus',
        ptt: true,
    });

    if (sendPath !== audioPath) {
        try { fs.unlinkSync(sendPath); } catch (e) {}
    }
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

export { sendText, sendAudio, sendButtons };
