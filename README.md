# TeraBox AI (Tera AI) 非公式 API

TeraBox の Web アプリ（`https://www.terabox.com/ai/agent`）を Playwright で解析して
特定した内部エンドポイントを、そのまま使える **Python クライアント + OpenAI 互換 API サーバー**
にまとめたものです。

公式 API ではありません。TeraBox 側の都合で仕様が変わる可能性があります。

---

## 1. 何ができるか

| やりたいこと | 使うもの |
|---|---|
| コマンドラインで Tera AI に質問する | `python terabox_ai.py "質問"` |
| Python から呼ぶ（ストリーミング対応） | `from terabox_ai import TeraBoxAI` |
| OpenAI 互換 API サーバーとして立てる | `uvicorn server:app` → `POST /v1/chat/completions` |
| 既存の OpenAI 用ツールから使う | `base_url=http://127.0.0.1:8000/v1`, `model="tera-ai"` |

思考過程（reasoning）と最終回答が別ストリームで流れてくるので、両方取得できます。

---

## 2. ファイル構成

```
terabox_ai/
├── terabox_ai.py     # クライアント本体（CLI 兼用・依存は requests のみ）
├── server.py         # FastAPI の OpenAI 互換サーバー
├── example.py        # 使い方サンプル
├── ENDPOINTS.md      # 解析して判明したエンドポイント仕様
├── requirements.txt
├── .env.example
└── cookies.txt       # ★自分で置く（同梱していません / Git に入れないこと）
```

---

## 3. セットアップ

```bash
pip install -r requirements.txt
```

### 認証について

TeraBox AI に API キーはありません。**ログイン済みブラウザのセッション Cookie**を使います。
重要なのは `ndus` の 1 個だけです。

指定方法は 3 通り、どれか 1 つでかまいません。

```bash
# (1) Netscape 形式の cookies.txt（ブラウザ拡張でエクスポート）※おすすめ
export TERABOX_COOKIE_FILE=./cookies.txt

# (2) ndus の値だけ
export TERABOX_NDUS=xxxxxxxxxxxxxxxxxxxx

# (3) Cookie ヘッダー文字列
export TERABOX_COOKIE='ndus=xxxx; browserid=yyyy'
```

`ndus` の取り方: TeraBox にログイン → DevTools → Application → Cookies →
`https://www.terabox.com` → `ndus` の値をコピー。

疎通確認:

```bash
python terabox_ai.py --whoami
# {"errno": 0, "uk": 4399668533344}   ← errno が 0 なら OK
```

---

## 4. CLI

```bash
python terabox_ai.py "日本の首都は？"
python terabox_ai.py --reasoning "素数を5つ挙げて"        # 思考過程も stderr に表示
python terabox_ai.py --chat-id 05de70132e716303e47c2561 "さっきの続きは？"
echo "長文をパイプで渡す" | python terabox_ai.py
```

---

## 5. Python から使う

```python
from terabox_ai import TeraBoxAI

client = TeraBoxAI(cookie_file="cookies.txt")   # or TeraBoxAI(ndus="...")

# ストリーミング
for piece in client.iter_text(prompt="3行で自己紹介して"):
    print(piece, end="", flush=True)

# 一括取得
res = client.chat(prompt="日本の首都は？")
print(res.text)        # 回答
print(res.reasoning)   # 思考過程
print(res.summary)     # 自動生成された会話タイトル
print(res.chat_id)     # 会話 ID（continuation に使う）

# 会話を続ける（履歴はサーバー側が chat_id で保持している）
next_res = client.chat(prompt="その人口は？", chat_id=res.chat_id)
print(next_res.text)

# 低レベル：SSE イベントをそのまま見る
for ev in client.stream(prompt="hello"):
    print(ev.event, ev.obj, ev.type, ev.status, repr(ev.text[:40]))
```

その他のメソッド: `check_login()` / `user_info()` / `quota()` /
`records()`（AI 履歴）/ `templates()`（サジェスト）/ `notes()`（AI ノート）/
`stop(chat_id, query_id)`（生成中断）。

---

## 6. API サーバーとして立てる

```bash
export TERABOX_COOKIE_FILE=./cookies.txt
# export API_KEY=local-dev-key      # このサーバー自体に Bearer 認証を掛けたい場合
uvicorn server:app --host 127.0.0.1 --port 8000
```

### OpenAI 互換エンドポイント

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"tera-ai","messages":[{"role":"user","content":"こんにちは"}]}'
```

ストリーミングは `"stream": true`（`chat.completion.chunk` 形式の SSE）。
`"include_reasoning": true` を付けると思考過程が `reasoning_content` デルタで流れます。
レスポンスの `terabox.chat_id` を次のリクエストの `"chat_id"` に渡すと会話が継続します。

公式 SDK からも使えます:

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="dummy")
print(client.chat.completions.create(
    model="tera-ai",
    messages=[{"role": "user", "content": "要約して: ..."}],
).choices[0].message.content)
```

### 独自エンドポイント

| Method | Path | 説明 |
|---|---|---|
| GET | `/health` | 死活監視 |
| GET | `/v1/models` | モデル一覧（`tera-ai`） |
| POST | `/v1/chat/completions` | OpenAI 互換チャット |
| POST | `/api/chat` | `{"prompt":"...","chat_id":null,"stream":false}` のシンプル版 |
| GET | `/api/me` | ログイン確認（`uk` を返す） |
| GET | `/api/quota` | ストレージ残量 |
| GET | `/api/records` | AI 利用履歴 |
| GET | `/api/templates` | サジェストプロンプト |
| POST | `/api/stop` | 生成中断 |

`http://127.0.0.1:8000/docs` に Swagger UI が出ます。

---

## 7. 仕様上の注意（解析で判明した点）

- **`messages` は必ず 1 件だけ**送る必要があります。2 件以上入れると `errno 2 / params error`
  で 400 になります。会話履歴はサーバー側が `chat_id` で保持する設計です。
  本クライアントは自動でこの制約を吸収します（`chat_id` あり → 最新の user 発話のみ送信、
  `chat_id` なし → 履歴を 1 つのプロンプトに畳んで送信）。
- 回答は SSE の `object=content, type=text, delta=true` チャンクの連結です。
  `type=reasoning` は思考過程で、`ping` は keep-alive なので無視して問題ありません。
- ブラウザは各リクエストに `jsToken` を付けますが、**Cookie を直接送る HTTP クライアントでは不要**でした。
- `POST /wfm/jnqp` はボット検知用のフィンガープリント送信で、API 利用には不要です。
- 詳細は `ENDPOINTS.md` を参照してください。

---

## 8. 免責

- 非公式・リバースエンジニアリングによる実装です。TeraBox の利用規約と
  レート制限を守って自己責任で利用してください。
- `cookies.txt` / `ndus` は**あなたのアカウントそのもの**です。リポジトリにコミットせず、
  共有もしないでください。漏れた場合は TeraBox でログアウト（全端末）してローテートしてください。
- エンドポイントは予告なく変更されます。動かなくなったら `ENDPOINTS.md` の手順で再解析してください。

---

## 9. 既存の OpenAI 用ツールから使う

`server.py` を起動して `base_url` を `http://127.0.0.1:8000/v1` に向けるだけで、
既存の OpenAI クライアントがそのまま使えます。API キーは任意の文字列で構いません
（サーバ側で `API_KEY` を設定したときのみ検証されます）。

```bash
export TERABOX_COOKIE_FILE=$PWD/cookies.txt
uvicorn server:app --host 127.0.0.1 --port 8000
```

### 動作検証済みクライアント

| ツール | バージョン | 非ストリーミング | ストリーミング |
|---|---|---|---|
| openai（Python） | 3.0.0 | OK | OK |
| openai（Node.js） | 7.4.0 | OK | OK |
| LangChain（`langchain-openai`） | 1.4.3 | OK | OK |
| LiteLLM | 1.96.2 | OK | OK |
| LlamaIndex（`llama-index-llms-openai`） | 0.7.10 | OK ※ | OK ※ |
| curl | 8.5.0 | OK | OK |

### Python（openai SDK）

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="dummy")
r = client.chat.completions.create(
    model="tera-ai",
    messages=[{"role": "user", "content": "こんにちは"}],
)
print(r.choices[0].message.content)
```

### Node.js（openai SDK）

```js
import OpenAI from "openai";

const client = new OpenAI({ baseURL: "http://127.0.0.1:8000/v1", apiKey: "dummy" });
const r = await client.chat.completions.create({
  model: "tera-ai",
  messages: [{ role: "user", content: "こんにちは" }],
});
console.log(r.choices[0].message.content);
```

### LangChain

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="tera-ai",
    base_url="http://127.0.0.1:8000/v1",
    api_key="dummy",
)
print(llm.invoke("OAuth 2.0 とは？").content)
```

LCEL チェーン（`prompt | llm | StrOutputParser()`）や `llm.stream()` もそのまま動きます。

### LiteLLM

```python
import litellm

r = litellm.completion(
    model="openai/tera-ai",
    api_base="http://127.0.0.1:8000/v1",
    api_key="dummy",
    messages=[{"role": "user", "content": "こんにちは"}],
)
print(r.choices[0].message.content)
```

### LlamaIndex ※注意

`llama-index-llms-openai` はリクエスト送信前にモデル名を自前のレジストリと照合するため、
`model="tera-ai"` だと `ValueError: Unknown model 'tera-ai'` で落ちます
（サーバ側の問題ではありません）。本サーバは `/v1/models` で `gpt-4o` / `gpt-4o-mini` /
`gpt-4.1` / `gpt-3.5-turbo` をエイリアスとして公開しているので、見慣れた名前を渡せば
そのまま使えます。モデル名はレスポンスにエコーされるだけで、実際のバックエンドは
常に Tera AI です。

```python
from llama_index.llms.openai import OpenAI as LIOpenAI

llm = LIOpenAI(
    model="gpt-4o",                       # エイリアス。中身は Tera AI
    api_base="http://127.0.0.1:8000/v1",
    api_key="dummy",
    is_chat_model=True,
    is_function_calling_model=False,
)
print(llm.complete("RAG とは？"))
```

エイリアス一覧は環境変数 `TERABOX_MODEL_ALIASES`（カンマ区切り）で変更できます。

### 未対応の機能

Tera AI 側に対応する概念がないため、`tools` / `function_call` の Function Calling、
`response_format` による JSON モード、`logprobs`、`n>1` は未対応です。
`temperature` や `max_tokens` はエラーにはなりませんが無視されます。
`usage` のトークン数は上流が返さないため常に 0 です。
