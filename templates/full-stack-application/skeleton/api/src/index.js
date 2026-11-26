import express from 'express';

const app = express();
app.use(express.json());
app.get('/api/health', (_request, response) => response.json({ status: 'ok' }));
app.get('/api', (_request, response) => response.json({ name: '${{ values.name }}' }));
app.listen(Number(process.env.PORT || 8080), '0.0.0.0');