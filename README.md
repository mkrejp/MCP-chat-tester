# MCP-chat-tester
light weight client for MCP interogation


Build a lightweight, modern web-based AI chat client that connects to the Hugging Face Inference API (using a free `HF_TOKEN`) and integrates with authenticated local and/or remote Model Context Protocol (MCP) servers (including RAG/knowledge-base MCP servers that return both text and images).

### Tech Stack & Architecture
- **Frontend:** Next.js (App Router) or React + Vite with Tailwind CSS. Keep the UI clean, minimal, and fast (similar to ChatGPT/Claude).
- **Backend / API Route:** Node.js / TypeScript backend endpoint (`/api/chat`) that handles streaming responses, manages authenticated MCP client connections, and routes tool calls between Hugging Face and the MCP servers.
- **MCP & AI SDK:** Use `@modelcontextprotocol/sdk` alongside `@huggingface/inference` (or `@huggingface/mcp-client` if compatible with custom auth headers and multimodal tool outputs). Using `@modelcontextprotocol/sdk` directly is preferred so we have full control over custom auth headers and multimodal `content` arrays (`text` + `image`).

### Core Features Required
1. **Configuration, Auth & Settings Drawer/Modal:**
   - Allow configuring the `HF_TOKEN` (defaulting to `process.env.HF_TOKEN` on the backend, with an optional override in the UI).
   - Allow selecting or typing a Hugging Face tool-capable model ID (default to `Qwen/Qwen2.5-72B-Instruct`, with presets like `meta-llama/Llama-3.3-70B-Instruct`).
   - Support adding/editing multiple MCP servers via a `mcp.config.json` file with **full authentication support**:
     - For `sse` / `streamable-http` servers: support `url` and custom `headers` (e.g., `"Authorization": "Bearer ${MCP_SERVER_TOKEN}"`).
     - For `stdio` servers: support `command`, `args`, and custom `env` variables (e.g., `"API_TOKEN": "${MCP_SERVER_TOKEN}"`).
     - Resolve `${ENV_VAR}` placeholders safely from `.env` on the backend.

2. **MCP & RAG Inspection Panel (Sidebar):**
   - Fetch and display all connected MCP servers, their auth/connection status (Connected / Auth Error / Offline), and transport type.
   - List all discovered **Tools**, **Resources**, and **Prompts** exposed by the connected MCP/RAG servers so I can inspect available capabilities.
   - Include a "Refresh MCP Servers" button to reload connections and tools without restarting the app.

3. **Intelligent Streaming Chat & Multimodal Image Rendering:**
   - Stream assistant responses token-by-token.
   - **Multimodal MCP Content Handling (Text + Images):**
     - When an MCP tool or RAG server returns a content array containing `image` items (e.g., `{ type: "image", data: "<base64>", mimeType: "image/png" }`) or image URLs alongside `text` items:
       1. Strip or summarize raw base64 image blobs before sending the tool result back to the Hugging Face text LLM so it does not overflow the model's context window.
       2. Forward the actual image(s) directly to the frontend UI stream and render them cleanly inside the chat message (with click-to-zoom/lightbox preview and download button).
   - **Tool & RAG Transparency:** Display a collapsible status badge/accordion inside the chat message showing:
     - Which MCP server and tool was called.
     - Input arguments sent to the tool.
     - Retrieved text chunks/citations AND thumbnails of any images returned by the MCP server.
   - Render Markdown properly (including code blocks with copy buttons and source citations).

4. **System Prompt Optimization:**
   - Include an editable system prompt pre-configured to instruct the model to query available MCP/RAG tools before answering, cite retrieved documents, and reference any visual charts/images returned by the tools.

### Project Setup & Deliverables
1. Scaffold the project cleanly with TypeScript.
2. Create an example `mcp.config.json` demonstrating:
   - An authenticated remote `SSE` / `HTTP` MCP RAG server using `"Authorization": "Bearer ${MCP_SERVER_TOKEN}"`.
   - An authenticated local `stdio` MCP server passing `"MCP_TOKEN": "${MCP_SERVER_TOKEN}"` via `env`.
3. Create a `.env.example` file with `HF_TOKEN=hf_...` and `MCP_SERVER_TOKEN=...`.
4. Add a concise `README.md` explaining how to configure tokens, connect MCP servers, and run the app locally.

Start by outlining the folder structure, installing the required dependencies (`@modelcontextprotocol/sdk`, `@huggingface/inference`, etc.), and building the authenticated MCP + multimodal handler on the backend first.

