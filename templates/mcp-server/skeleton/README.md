# ${{ values.name }}

${{ values.description }}

This project uses the official TypeScript MCP SDK v2 and starts with stdio transport and a `${{ values.toolName }}` tool.

## Requirements

- Node.js 22 or newer
- An MCP-compatible client that can launch a local command

## Build and test

```bash
npm install
npm test
```

## Run

```bash
npm start
```

The server communicates over standard input/output. Configure your MCP client to launch `node` with `dist/index.js` as its argument, using this project as the working directory. Do not print logs to stdout; stdout carries MCP protocol messages.

## Extend the server

Add tools with `server.registerTool` in `src/index.ts`. Define argument schemas with Zod, keep handlers focused, and return MCP content blocks. The SDK also supports resources and prompts; register those with the corresponding `McpServer` methods. Add business logic under `src/` and tests under `test/`.

This starter is stdio-only. If you expose an HTTP transport, configure authentication and host/origin protections before making it reachable. Keep credentials in environment variables or a secret manager, never in source control.