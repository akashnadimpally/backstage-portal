import express from 'express';

const app = express();
app.use(express.json({ limit: '256kb' }));
app.get('/health', (_request, response) => response.json({ status: 'ok' }));
app.post('/events', async (request, response, next) => {
  const event = request.body;
  if (!event || typeof event.id !== 'string' || typeof event.type !== 'string') {
    return response.status(400).json({ error: 'Expected an event with string id and type fields' });
  }
  try {
    await handleEvent(event);
    return response.status(202).json({ accepted: true, id: event.id });
  } catch (error) {
    return next(error);
  }
});

async function handleEvent(event) {
  console.info('Received event', { id: event.id, type: event.type });
}

app.listen(Number(process.env.PORT || 8080), '0.0.0.0');