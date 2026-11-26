import { McpServer } from '@modelcontextprotocol/server';
import { StdioServerTransport } from '@modelcontextprotocol/server/stdio';
import * as z from 'zod/v4';
import { greet } from './tools/greet.js';

export function createServer(): McpServer {
  const server = new McpServer({
    name: '${{ values.name }}',
    version: '1.0.0',
  });

  server.registerTool(
    '${{ values.toolName }}',
    {
      description: '${{ values.toolDescription }}',
      inputSchema: z.object({
        name: z.string().min(1).max(100).describe('Name to greet'),
      }),
    },
    async ({ name }) => ({
      content: [{ type: 'text', text: greet(name) }],
    }),
  );

  return server;
}

async function main(): Promise<void> {
  const server = createServer();
  await server.connect(new StdioServerTransport());
}

main().catch(error => {
  console.error('MCP server failed to start:', error);
  process.exitCode = 1;
});