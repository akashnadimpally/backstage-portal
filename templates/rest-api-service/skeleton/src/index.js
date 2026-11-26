import express from 'express';

const app = express();
const port = Number(process.env.PORT || ${{ values.port }});

app.use(express.json());
app.get('/health', (_request, response) => response.json({ status: 'ok' }));
app.get('/api/v1', (_request, response) => {
  response.json({ service: '${{ values.name }}', message: 'Implement your API here.' });
});

app.listen(port, '0.0.0.0', () => {
  console.log(`Listening on port ${port}`);
});