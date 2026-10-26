# n8n Evidence Webhook Integration

PhoenixRepo provides an optional, external integration with [n8n](https://n8n.io/) to enable low-code evidence gathering and webhooks.

> [!NOTE]
> The n8n integration is completely **optional and off by default**. When the `N8N_WEBHOOK_URL` environment variable is unset, the `n8n` tool is disabled and calls immediately return `ok=False` with error `"n8n disabled"`.

---

## 1. Running n8n Locally

You can launch a local n8n instance using Docker:

```bash
docker run -it --rm --name n8n -p 5678:5678 n8nio/n8n
```

Once running, access the n8n UI at:
```
http://localhost:5678
```

---

## 2. Importing the Workflow

1. In the n8n UI, navigate to **Workflows**.
2. Click **Add workflow** or click the **...** menu in the top right and select **Import from File**.
3. Choose the file located at [`n8n/phoenix_evidence_webhook.json`](file:///C:/Users/nn201/phoenixrepo/n8n/phoenix_evidence_webhook.json).
4. The workflow consists of three nodes:
   - **Webhook**: Listens for HTTP POST requests on `/phoenix-evidence`.
   - **HTTP Request**: Fetches the `url` specified in the incoming payload.
   - **Respond to Webhook**: Returns `{ url, status, text_excerpt }`.

---

## 3. Activating and Configuring

1. Toggle the workflow to **Active** in the n8n UI.
2. Set the environment variable in your terminal or `.env`:
   ```bash
   N8N_WEBHOOK_URL=http://localhost:5678/webhook/phoenix-evidence
   ```
3. Test with curl or via the toolbox:
   ```bash
   curl -X POST http://localhost:5678/webhook/phoenix-evidence \
     -H "Content-Type: application/json" \
     -d '{"url": "https://api.github.com/zen"}'
   ```
