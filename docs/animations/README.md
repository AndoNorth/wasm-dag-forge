# Animations

Motion Canvas projects that explain the job system.
The rendered GIFs in `media/` are embedded in the top-level README and docs.

| Project | Source | Output |
|---------|--------|--------|
| `forge-job` | `src/scenes/forge-job.tsx` | `media/job-walkthrough.gif`, embedded in the README |
| `forge-batch` | `src/scenes/forge-batch.tsx` | not rendered yet, stored for a later README section |

`forge-batch` shows 3 workflows, each run as a batch of 100,000 jobs with random seeds, followed by one aggregate-only job.
A batch-wide aggregate is still an open design question, so treat that scene as a sketch until it is settled.

## Re-render

```sh
npm install
npm run dev
```

1. Open `http://localhost:9100/src/forge-job` and check the preview.
2. Click **Render** in the Video Settings panel.
   The project meta renders at 15 fps and half scale, as PNG frames into `output/forge-job/` (gitignored).
3. Encode the frames, with any ffmpeg:

```sh
ffmpeg -y -framerate 15 -i output/forge-job/%06d.png \
  -vf "split[a][b];[a]palettegen=max_colors=64:stats_mode=diff[p];[b][p]paletteuse=dither=none:diff_mode=rectangle" \
  -loop 0 media/job-walkthrough.gif
```

The result is about 230 KB, so keep the committed GIF small.
Change the animation text when the design changes, so the GIF never drifts from `docs/architecture/`.
