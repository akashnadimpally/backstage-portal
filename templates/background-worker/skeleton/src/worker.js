let stopping = false;
process.once('SIGINT', () => { stopping = true; });
process.once('SIGTERM', () => { stopping = true; });

async function processNextJob() {
  // Replace with a queue client and acknowledge jobs only after successful processing.
  await new Promise(resolve => setTimeout(resolve, 1000));
}

while (!stopping) {
  try {
    await processNextJob();
  } catch (error) {
    console.error('Job processing failed', error);
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
}

console.info('Worker stopped cleanly');