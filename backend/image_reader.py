import asyncio
import base64
import json
import logging
import os
import tempfile
from typing import Optional

import ollama

from backend.config import settings

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}
PDF_EXTENSIONS = {".pdf"}


def _pdf_to_images(pdf_path: str) -> list[str]:
    image_paths = []
    try:
        from pdf2image import convert_from_path
        pages = convert_from_path(pdf_path, dpi=200)
        tmp_dir = tempfile.mkdtemp(prefix="pdf_pages_")
        for i, page in enumerate(pages):
            out_path = os.path.join(tmp_dir, f"page_{i+1}.jpg")
            page.save(out_path, "JPEG")
            image_paths.append(out_path)
        logger.info(f"Converted PDF {pdf_path} to {len(image_paths)} images")
    except ImportError:
        logger.warning("pdf2image not installed, cannot convert PDF. Install: pip install pdf2image")
    except Exception as e:
        logger.error(f"PDF to image conversion failed: {e}")
    return image_paths


def _encode_image(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _is_image_file(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def _is_pdf_file(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    return ext in PDF_EXTENSIONS


async def read_image(image_path: str, context: str = "general") -> dict:
    if not image_path or not os.path.exists(image_path):
        return {"success": False, "error": "File not found", "data": {}}

    image_paths = []
    cleanup_paths = []

    if _is_pdf_file(image_path):
        converted = _pdf_to_images(image_path)
        if not converted:
            return {"success": False, "error": "PDF conversion failed", "data": {}}
        image_paths = converted
        cleanup_paths = converted
    elif _is_image_file(image_path):
        image_paths = [image_path]
    else:
        return {"success": False, "error": f"Unsupported file type: {os.path.splitext(image_path)[1]}", "data": {}}

    prompt_map = {
        "id_card": (
            "You are reading an ID card image (Aadhaar, driver's licence, PAN, voter ID, passport, etc). "
            "Extract the following as JSON:\n"
            '- "name": full name on the card\n'
            '- "id_type": type of ID (Aadhaar/Licence/PAN/Voter ID/Passport/Other)\n'
            '- "id_number": the ID number or Aadhaar number\n'
            '- "dob": date of birth if present\n'
            '- "address": address if present\n'
            '- "gender": gender if present\n'
            'Return ONLY valid JSON, no extra text.'
        ),
        "prescription": (
            "You are reading a medical prescription or medical report image. "
            "Extract the following as JSON:\n"
            '- "doctor_name": name of the prescribing doctor\n'
            '- "patient_name": patient name if mentioned\n'
            '- "date": date of prescription/report\n'
            '- "medicines": list of medicines with dosage if readable\n'
            '- "diagnosis": diagnosis or condition mentioned\n'
            '- "notes": any additional notes, advice, or instructions\n'
            '- "report_type": type of report if this is a lab report (blood test, ultrasound, etc)\n'
            '- "key_values": any key test values with units if this is a lab report\n'
            'Return ONLY valid JSON, no extra text.'
        ),
        "payment_screenshot": (
            "You are reading a payment screenshot (UPI, bank transfer, Google Pay, PhonePe, Paytm, etc). "
            "Extract the following as JSON:\n"
            '- "payment_status": "success" or "failed" or "pending" based on what you see\n'
            '- "amount": the payment amount as a string (e.g. "750", "1000")\n'
            '- "transaction_id": UPI transaction reference number or ID\n'
            '- "payment_method": "UPI" or "Google Pay" or "PhonePe" or "Paytm" or "Bank Transfer" or "Other"\n'
            '- "date_time": date and time of payment if visible\n'
            '- "recipient": name or UPI ID of the person who received payment\n'
            'Return ONLY valid JSON, no extra text.'
        ),
        "report": (
            "You are reading a medical report or lab test report image. "
            "Extract the following as JSON:\n"
            '- "report_type": type of report (blood test, urine test, ultrasound, X-ray, MRI, CT scan, etc)\n'
            '- "patient_name": patient name if mentioned\n'
            '- "date": date of report\n'
            '- "doctor_name": referring doctor name if mentioned\n'
            '- "key_findings": list of key findings or abnormal values\n'
            '- "key_values": important test values with units and reference ranges if readable\n'
            '- "summary": brief summary of the report findings\n'
            'Return ONLY valid JSON, no extra text.'
        ),
        "general": (
            "You are reading an image sent during a medical clinic conversation. "
            "Determine what type of image this is and extract relevant information as JSON:\n"
            '- "image_type": one of "id_card", "prescription", "payment_screenshot", "report", "other"\n'
            '- "summary": brief description of what is in the image\n'
            '- "extracted_data": any relevant data extracted from the image\n'
            'Return ONLY valid JSON, no extra text.'
        ),
    }

    prompt = prompt_map.get(context, prompt_map["general"])

    all_results = []
    for img_path in image_paths:
        try:
            b64 = _encode_image(img_path)

            def _ollama_read(b64_data, prompt_text):
                client = ollama.Client(host=settings.OLLAMA_HOST)
                return client.chat(
                    model=settings.OLLAMA_VISION_MODEL or settings.OLLAMA_MODEL,
                    messages=[
                        {
                            "role": "user",
                            "content": prompt_text,
                            "images": [b64_data],
                        }
                    ],
                    options={"temperature": 0.1, "num_predict": 1024},
                )

            response = await asyncio.to_thread(_ollama_read, b64, prompt)
            raw = response["message"]["content"].strip()
            import re
            json_match = re.search(r'\{[\s\S]*\}', raw)
            if json_match:
                parsed = json.loads(json_match.group())
            else:
                parsed = {"raw_text": raw}
            all_results.append(parsed)
        except Exception as e:
            logger.error(f"Image reading failed for {img_path}: {e}")
            all_results.append({"error": str(e)})

    for p in cleanup_paths:
        try:
            os.remove(p)
        except Exception:
            pass

    if len(all_results) == 1:
        result_data = all_results[0]
    else:
        result_data = {"pages": all_results}

    return {"success": True, "data": result_data, "image_path": image_path}


async def classify_image_type(image_path: str) -> str:
    if not image_path or not os.path.exists(image_path):
        return "general"

    try:
        b64 = _encode_image(image_path)

        def _ollama_classify(b64_data):
            client = ollama.Client(host=settings.OLLAMA_HOST)
            return client.chat(
                model=settings.OLLAMA_VISION_MODEL or settings.OLLAMA_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Classify this image into exactly one of these categories: "
                            "id_card, prescription, payment_screenshot, report, other. "
                            "Reply with ONLY the category name, nothing else."
                        ),
                        "images": [b64_data],
                    }
                ],
                options={"temperature": 0.0, "num_predict": 20},
            )

        response = await asyncio.to_thread(_ollama_classify, b64)
        classification = response["message"]["content"].strip().lower()
        valid = {"id_card", "prescription", "payment_screenshot", "report", "other"}
        if classification in valid:
            return classification
        for v in valid:
            if v in classification:
                return v
        return "general"
    except Exception as e:
        logger.error(f"Image classification failed: {e}")
        return "general"


async def read_image_with_classification(image_path: str) -> dict:
    if not image_path or not os.path.exists(image_path):
        return {"success": False, "error": "File not found", "data": {}, "image_type": "general"}

    if _is_pdf_file(image_path):
        converted = _pdf_to_images(image_path)
        if not converted:
            return {"success": False, "error": "PDF conversion failed", "data": {}, "image_type": "pdf"}
        img_type = await classify_image_type(converted[0])
        for p in converted[1:]:
            try:
                os.remove(p)
            except Exception:
                pass
        result = await read_image(converted[0], context=img_type)
        result["image_type"] = img_type
        return result

    img_type = await classify_image_type(image_path)
    result = await read_image(image_path, context=img_type)
    result["image_type"] = img_type
    return result
