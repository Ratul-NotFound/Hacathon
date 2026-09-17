import os
import time
from dotenv import load_dotenv
from groq import Groq

load_dotenv()
api_key = os.environ.get("GROQ_API_KEY")

client = Groq(api_key=api_key)

models = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
prompt = 'Interpret this note: "Facilities will wash rooftop solar from noon until 2 PM. Usable solar should be treated as 25% of forecast." Return JSON: [{"directive_type": "solar_reduction", "hours": [12, 13], "factor": 0.25}]'

print(f"{'MODEL':<25} | {'LATENCY':<8} | {'STATUS'}")
print("-" * 50)
for m in models:
    t0 = time.time()
    try:
        r = client.chat.completions.create(
            model=m,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )
        dt = time.time() - t0
        print(f"{m:<25} | {dt:.2f}s    | SUCCESS: {r.choices[0].message.content.strip()[:60]}...")
    except Exception as e:
        dt = time.time() - t0
        print(f"{m:<25} | {dt:.2f}s    | ERROR: {e}")
