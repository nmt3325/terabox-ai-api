"""Minimal usage examples for terabox_ai."""
from terabox_ai import TeraBoxAI

client = TeraBoxAI(cookie_file="cookies.txt")   # or TeraBoxAI(ndus="...")

# 0) verify the session
print("uk =", client.check_login().get("uk"))

# 1) streaming
for piece in client.iter_text(prompt="Give me 3 study tips."):
    print(piece, end="", flush=True)
print()

# 2) one shot, with reasoning
res = client.chat(prompt="What is the capital of Japan?")
print(res.text)
print("chat_id:", res.chat_id)

# 3) continue the same conversation
follow = client.chat(
    messages=[
        {"role": "user", "content": "What is the capital of Japan?"},
        {"role": "assistant", "content": res.text},
        {"role": "user", "content": "And its population?"},
    ],
    chat_id=res.chat_id,
)
print(follow.text)
