import assert from 'node:assert/strict';
import test from 'node:test';
import { greet } from '../dist/tools/greet.js';

test('greet returns a greeting for the supplied name', () => {
  assert.equal(greet('Ada'), 'Hello, Ada!');
});