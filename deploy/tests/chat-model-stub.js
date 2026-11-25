// A stand-in for what LibreChat's default model spec talks to: the "Klai AI"
// chat endpoint and the klai-knowledge MCP server.
//
// Used by deploy/tests/chat-config-boot.sh when a screen check runs against a
// freshly built chat image: the screen needs one finished assistant answer
// before it shows the regenerate and feedback controls the production
// journeys use, and neither a real model nor the knowledge service is
// available or wanted in CI. Runs with the node binary inside the chat image
// itself, so it adds no image.
//
//   POST /v1/chat/completions  OpenAI chat completions, streamed (the answer)
//                              or not (the title); always the same fixed text,
//                              never a tool call, so a check can look for it.
//   POST /mcp                  MCP over streamable HTTP, JSON responses: the
//                              klai-knowledge spec refuses to answer when the
//                              server lists no tools ("The agent is configured
//                              to use MCP tools, but none are available"), so
//                              it lists one search tool.
const http = require('http')

const REPLY = process.env.STUB_REPLY || 'Hallo van het testmodel.'

function chat(request, res) {
  const base = { id: 'chatcmpl-stub', created: Math.floor(Date.now() / 1000), model: request.model || 'stub' }
  if (!request.stream) {
    res.writeHead(200, { 'content-type': 'application/json' })
    res.end(
      JSON.stringify({
        ...base,
        object: 'chat.completion',
        choices: [{ index: 0, message: { role: 'assistant', content: REPLY }, finish_reason: 'stop' }],
        usage: { prompt_tokens: 1, completion_tokens: 5, total_tokens: 6 },
      }),
    )
    return
  }
  const chunk = (delta, finish) =>
    `data: ${JSON.stringify({ ...base, object: 'chat.completion.chunk', choices: [{ index: 0, delta, finish_reason: finish }] })}\n\n`
  res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' })
  res.write(chunk({ role: 'assistant', content: '' }, null))
  res.write(chunk({ content: REPLY }, null))
  res.write(chunk({}, 'stop'))
  res.end('data: [DONE]\n\n')
}

const MCP_RESULTS = {
  initialize: (params) => ({
    protocolVersion: (params && params.protocolVersion) || '2025-03-26',
    capabilities: { tools: {} },
    serverInfo: { name: 'knowledge-stub', version: '1.0.0' },
  }),
  ping: () => ({}),
  'tools/list': () => ({
    tools: [
      {
        name: 'search_knowledge',
        description: 'Search the knowledge base (screen-check stub, returns nothing).',
        inputSchema: { type: 'object', properties: { query: { type: 'string' } }, required: ['query'] },
      },
    ],
  }),
  'tools/call': () => ({ content: [{ type: 'text', text: 'No results.' }] }),
}

function mcp(message, res) {
  // Notifications carry no id and get no answer.
  if (message.id === undefined) {
    res.writeHead(202)
    res.end()
    return
  }
  const result = MCP_RESULTS[message.method]
  const reply = result
    ? { jsonrpc: '2.0', id: message.id, result: result(message.params) }
    : { jsonrpc: '2.0', id: message.id, error: { code: -32601, message: `unknown method ${message.method}` } }
  res.writeHead(200, { 'content-type': 'application/json', 'mcp-session-id': 'screen-check' })
  res.end(JSON.stringify(reply))
}

http
  .createServer((req, res) => {
    let body = ''
    req.on('data', (chunk) => {
      body += chunk
    })
    req.on('end', () => {
      console.log(`${req.method} ${req.url} ${body.slice(0, 120)}`)
      const path = req.url.split('?')[0]
      if (req.method === 'POST' && path.endsWith('/chat/completions')) return chat(JSON.parse(body || '{}'), res)
      if (req.method === 'POST' && path === '/mcp') return mcp(JSON.parse(body || '{}'), res)
      // A GET on /mcp asks for a server-sent event stream, which this stub
      // does not offer; 405 is how the transport says so.
      res.writeHead(path === '/mcp' ? 405 : 404)
      res.end()
    })
  })
  .listen(8080, () => console.log('chat model stub listening on 8080'))
