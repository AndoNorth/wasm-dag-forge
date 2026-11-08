import motionCanvas from '@motion-canvas/vite-plugin';
import {defineConfig} from 'vite';

export default defineConfig({
  plugins: [motionCanvas({project: ['./src/forge-job.ts', './src/forge-batch.ts']})],
});
