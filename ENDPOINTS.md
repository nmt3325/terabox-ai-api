# TeraBox AI - reverse engineered endpoint map

Host: `https://www.terabox.com`
Auth: session cookies (`ndus` is the critical one). Most endpoints also accept
the common query params `app_id=250528&web=1&channel=dubox&clienttype=0`.
Some browser-issued calls add `jsToken=<hex>`, but it is NOT required when the
cookie jar is sent directly.

## Session
| Method | Path | Notes |
|---|---|---|
| GET | `/api/check/login` | `{"errno":0,"uk":<user id>}` when the session is valid |
| GET | `/api/user/getinfo` | account profile |
| GET | `/api/quota` | storage quota |
| GET | `/passport/get_info` | passport profile |

## Tera AI chat (agent)
| Method | Path | Notes |
|---|---|---|
| POST | `/ai/proxy/agent/stream?language=en&client_source=1&clienttype=0` | **main chat endpoint**, SSE |
| GET | `/ai/proxy/agent/template` | suggested prompts |
| GET | `/ai/proxy/agent/queryinfo` | requires `chat_id` + `query_id` |
| GET | `/ai/proxy/record/list` | AI history, cursor paginated |
| POST | `/ai/proxy/chat/stop` | abort a running generation |
| POST | `/ai/proxy/chat/create` `/chat/detail` `/chat/edit` `/chat/getstream` `/chat/genflowstream` | conversation management |
| POST | `/ai/proxy/agent/filetransfer` `/agent/filedownload` `/agent/share` | move AI output into My Cloud |

### Request body of `/ai/proxy/agent/stream`
```json
{
  "messages": [{"role": "user", "content": "Hello", "attachments": []}],
  "is_first": true,
  "is_rechat": false,
  "chat_id": null,
  "chat_scene": 0
}
```
Send `chat_id` (the `echat_id` from a previous turn) with `is_first: false` to
continue a conversation.

### Response (SSE, `text/event-stream`)
Every frame is `data: {"event": ..., "time": ..., "logId": ..., "data": {...}}`.

* `event`: `start` -> `generating` (xN, interleaved with `ping`) -> `end`
* `data.echat_id` / `data.equery_id` / `data.message_id`: conversation ids
* `data.result`: a **JSON-encoded string**, parse it again. Fields:
  * `object`: `response` | `message` | `content`
  * `type`: `reasoning` | `text` | `summary` | `non-textblock`
  * `status`: `created` | `in_progress` | `completed`
  * `delta`: `true` for incremental chunks, `false` for the consolidated block
  * `text`: the actual chunk

So the answer is the concatenation of `object=content, type=text, delta=true`
chunks; the model's chain of thought arrives as `type=reasoning`.
The final `response/completed` frame carries the whole message array, and a
`message/summary` frame carries an auto generated conversation title.

## Other AI products (same auth, discovered in the web bundle)
* Presentation maker: `/ai/proxy/ppt/genoutline`, `/ppt/genppt`, `/ppt/querytask`,
  `/pptx/save`, `/pptx/smartedit`, `/pptx/download`, `/pptbeautify/submit`,
  `/ppthtml/genoutline`, `/ppthtml/genslide`, `/nanoppt/genoutline`, `/nanoppt/genslide`
* AI Notebook (Study Hall): `/ai/proxy/studyhall/notelist`, `/noteadd`, `/noteupdate`,
  `/notesummarylist`, `/studiolist`, `/studioadd`, `/studioget`, `/sharenote`
* Writing: `/ai/proxy/write/genoutline`, `/write/genthesis`, `/rewrite/send`
* Research report: `/ai/proxy/researchreport/genoutline`, `/genfile`, `/download`
* Docs / HTML artifacts: `/ai/proxy/doc/*`, `/ai/proxy/html/*`
* Files: `/ai/proxy/file/detail`, `/file/detaillist`, `/file/update`
* Transcribe: `/aitranscribe/record/unexposed`, `/aitranscribe/privilege/all`

## Notes
* `POST /wfm/jnqp` is an anti-bot fingerprint beacon fired by the SPA. It is not
  required for API access.
* The SPA drops the `ndus` cookie on `/ai/*` routes when it decides it is logged
  out, which is why browser automation needs the cookie re-injected before each
  navigation. Plain HTTP clients are unaffected.
