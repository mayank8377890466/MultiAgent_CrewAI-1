"""Vercel ASGI entrypoint.
Streamlit uses Tornado + WebSockets — it cannot run on Vercel serverless functions.
This serves a landing page directing you to a compatible platform.
"""

LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TestFixer — Platform Notice</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         max-width: 640px; margin: 80px auto; padding: 0 24px; line-height: 1.6;
         color: #1a1a1a; background: #f9fafb; }
  .card { background: #fff; border-radius: 12px; padding: 32px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
  h1 { margin-top: 0; color: #0f172a; }
  code { background: #f1f5f9; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }
  a { color: #2563eb; }
  ul { padding-left: 20px; }
  li { margin-bottom: 8px; }
</style>
</head>
<body>
<div class="card">
<h1>⚠️ Vercel cannot run Streamlit + CrewAI</h1>
<p>
  <strong>TestFixer</strong> uses <strong>Streamlit</strong> (which needs persistent WebSocket
  connections) and <strong>CrewAI agents</strong> (which can run for several minutes).
</p>
<p>
  Vercel serverless functions have a strict timeout (10s free / 60s Pro) and do not
  support WebSockets — this app <em>cannot</em> run here.
</p>
<p><strong>Deploy to one of these platforms instead:</strong></p>
<ul>
  <li><a href="https://render.com">Render</a> — Web Service with Docker support</li>
  <li><a href="https://railway.app">Railway</a> — simple <code>railway up</code></li>
  <li><a href="https://fly.io">Fly.io</a> — <code>fly launch</code></li>
  <li><a href="https://streamlit.io/cloud">Streamlit Cloud</a> — built for Streamlit</li>
</ul>
<p style="color: #64748b; font-size: 0.85em; margin-top: 24px;">
  Repo:
  <a href="https://github.com/mayank8377890466/MultiAgent_CrewAI-1">
    github.com/mayank8377890466/MultiAgent_CrewAI-1
  </a>
</p>
</div>
</body>
</html>
"""


async def app(scope, receive, send):
    if scope["type"] != "http":
        return

    body = LANDING_HTML.encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"content-type", b"text/html; charset=utf-8"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({
        "type": "http.response.body",
        "body": body,
    })
