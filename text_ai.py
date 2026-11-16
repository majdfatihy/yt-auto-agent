"""Shared fail-closed JSON generation for the bot's existing SEO/title flow.

Primary: supported Gemini models. Backup: existing GROQ_API_KEY (chat, not ASR).
Never silently fall back to unreviewed raw transcript snippets for publication.
"""
import json
import os
import requests

GEMINI_DEFAULTS = ("gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite")
GROQ_DEFAULTS = ("openai/gpt-oss-120b", "openai/gpt-oss-20b")


def _model_names(env_var, defaults):
    raw = os.getenv(env_var, "").strip()
    return list(dict.fromkeys(s.strip() for s in (raw.split(",") if raw else defaults) if s.strip()))[:5]


def _gemini_json(model, prompt, key):
    from google import genai
    client = genai.Client(api_key=key)
    response = client.models.generate_content(
        model=model, contents=prompt, config={"response_mime_type": "application/json"})
    return json.loads(response.text)


def _groq_json(model, prompt, key):
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        json={"model": model,
              "messages": [
                  {"role": "system", "content": "Return strictly valid JSON. Follow the Arabic content and factuality constraints. Never invent topics outside the provided transcript."},
                  {"role": "user", "content": prompt}
              ],
              "response_format": {"type": "json_object"},
              "max_completion_tokens": 3400},
        timeout=(10, 60))
    response.raise_for_status()
    body = response.json()
    return json.loads(body["choices"][0]["message"]["content"])


def generate_json(prompt, gemini_key, env_var, validator, *, groq_key=None):
    """Return (validated result, provider). Validator must reject weak/unfounded output.

    If neither provider returns a valid response, fail visibly before saving a plan.
    """
    failures = []
    if gemini_key:
        for model in _model_names(env_var, GEMINI_DEFAULTS):
            try:
                data = validator(_gemini_json(model, prompt, gemini_key))
                print("TEXT_AI: GEMINI_SUCCESS: " + model)
                return data, "gemini:" + model
            except Exception as error:
                failures.append("gemini")
                print("TEXT_AI: GEMINI_RETRY: " + model + ": " + type(error).__name__ + ": " + str(error)[:100])
    token = (groq_key if groq_key is not None else os.getenv("GROQ_API_KEY", "")).strip()
    if token:
        for model in _model_names("GROQ_TEXT_MODELS", GROQ_DEFAULTS):
            try:
                data = validator(_groq_json(model, prompt, token))
                print("TEXT_AI: GROQ_SUCCESS: " + model)
                return data, "groq:" + model
            except Exception as error:
                failures.append("groq")
                print("TEXT_AI: GROQ_RETRY: " + model + ": " + type(error).__name__ + ": " + str(error)[:100])
    raise RuntimeError(
        "REEL_TEXT_AI_UNAVAILABLE: لم يتم توليد عناوين موثوقة من Gemini أو Groq؛ "
        "لن تُعرض أو تُنشر عناوين مقتطعة عشوائيًا. تحقق من صلاحية النماذج والرصيد. "
        "(providers attempted: " + ",".join(sorted(set(failures))) + ")")
