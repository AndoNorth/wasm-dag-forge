import {Circle, Rect, Txt, makeScene2D} from '@motion-canvas/2d';
import {
  all,
  createRef,
  createSignal,
  easeInOutCubic,
  linear,
  range,
  sequence,
  waitFor,
} from '@motion-canvas/core';
import {C, box, fmt} from './forge-kit';

const SEEDS = 100000;
const rows = [
  {name: 'Workflow A', color: C.blue, y: -230, speed: 1},
  {name: 'Workflow B', color: C.pink, y: 0, speed: 1.3},
  {name: 'Workflow C', color: C.amber, y: 230, speed: 1.7},
];
const colX = [-780, -470, -160, 150];

export default makeScene2D(function* (view) {
  view.fill(C.bg);

  const caption = createRef<Txt>();
  const say = (t: string) => caption().text(t);

  const queued = rows.map(() => createSignal(0));
  const done = rows.map(() => createSignal(0));
  const indexed = createSignal(0);

  const tpl = rows.map(() => createRef<Rect>());
  const q = rows.map(() => createRef<Rect>());
  const run = rows.map(() => createRef<Rect>());
  const res = rows.map(() => createRef<Rect>());
  const index = createRef<Rect>();
  const agg = createRef<Rect>();
  const summaries = rows.map(() => createRef<Rect>());
  const dots = rows.map(() => range(6).map(() => createRef<Circle>()));
  const note = createRef<Txt>();

  view.add(
    <>
      <Txt
        text="3 workflows x 100,000 random seeds, one aggregate"
        fill={C.text}
        fontSize={44}
        fontWeight={700}
        x={-250}
        y={-470}
      />
      <Txt
        text="solid = has code/contract   dashed = designed only"
        fill={C.dim}
        fontSize={22}
        x={640}
        y={-470}
      />
      {rows.map((r, i) =>
        box({ref: tpl[i], x: colX[0], y: r.y, w: 230, h: 130, title: r.name, sub: 'template: validated once, compiled once', color: r.color}),
      )}
      {rows.map((r, i) =>
        box({ref: q[i], x: colX[1], y: r.y, w: 230, h: 150, title: 'intake queue', sub: 'jobs waiting', color: r.color, designed: true}),
      )}
      {rows.map((r, i) =>
        box({ref: run[i], x: colX[2], y: r.y, w: 230, h: 150, title: 'admitted jobs', sub: 'slot-limited workers', color: r.color, designed: true}),
      )}
      {rows.map((r, i) =>
        box({ref: res[i], x: colX[3], y: r.y, w: 230, h: 150, title: 'results', sub: 'jobs done', color: r.color}),
      )}
      {rows.map((r, i) => (
        <Txt
          fill={r.color}
          fontSize={34}
          fontWeight={700}
          x={colX[1]}
          y={r.y + 50}
          text={() => fmt(queued[i]())}
          opacity={() => q[i]().opacity()}
        />
      ))}
      {rows.map((r, i) => (
        <Txt
          fill={r.color}
          fontSize={34}
          fontWeight={700}
          x={colX[3]}
          y={r.y + 50}
          text={() => fmt(done[i]())}
          opacity={() => res[i]().opacity()}
        />
      ))}
      {rows.map((r, i) =>
        dots[i].map(d => (
          <Circle ref={d} width={18} height={18} fill={r.color} opacity={0} x={colX[0] + 115} y={r.y} />
        )),
      )}
      {box({ref: index, x: 410, y: 0, w: 180, h: 200, title: 'results index', sub: 'JSONL, streamed', color: C.green})}
      <Txt
        fill={C.green}
        fontSize={34}
        fontWeight={700}
        x={410}
        y={60}
        text={() => fmt(indexed())}
        opacity={() => index().opacity()}
      />
      {box({ref: agg, x: 625, y: 0, w: 180, h: 200, title: 'aggregate', sub: 'aggregate-only job, summary_stats', color: C.amber})}
      {rows.map((r, i) =>
        box({ref: summaries[i], x: 840, y: r.y, w: 160, h: 80, title: `summary ${r.name.slice(-1)}`, color: r.color}),
      )}
      <Txt
        ref={note}
        text="Cross-job aggregate is an open design question in the repo (#17)"
        fill={C.amber}
        fontSize={22}
        x={560}
        y={-380}
        opacity={0}
      />
      <Txt
        ref={caption}
        text=""
        fill={C.text}
        fontSize={28}
        x={0}
        y={440}
        width={1700}
        textWrap
        textAlign="center"
      />
    </>,
  );

  yield* waitFor(2);

  // 1. templates
  say('Three templates, one per workflow. Each is validated once and stored once by manifest hash; the compile result is shared by every job in its batch.');
  yield* sequence(0.3, ...tpl.map(t => t().opacity(1, 0.5)));
  yield* waitFor(3);

  // 2. seeds -> intake
  say('Each batch submits 100,000 jobs: same manifest, one random seed each (0 <= seed < 2^53). Every job gets an accepted row and an intake event.');
  yield* all(...q.map(r => r().opacity(1, 0.5)));
  yield* all(
    ...rows.map((r, i) =>
      all(
        queued[i](SEEDS, 4, easeInOutCubic),
        sequence(0.25, ...dots[i].map(d =>
          all(
            d().opacity(1, 0.1),
            d().position.x(colX[1], 1.0, linear),
            d().opacity(0, 0.1),
          ),
        )),
      ),
    ),
  );
  yield* waitFor(2);

  // 3. admission + run
  say('Layer 1 admission releases jobs as capacity allows. The backlog drains while workers finish jobs, at different rates per workflow.');
  yield* all(...run.map(r => r().opacity(1, 0.5)), ...res.map(r => r().opacity(1, 0.5)));
  yield* all(
    ...rows.map((r, i) =>
      all(
        queued[i](0, 8 / r.speed, linear),
        done[i](SEEDS, 8 / r.speed, linear),
      ),
    ),
  );
  yield* waitFor(2);

  // 4. index
  say('The three result sets are written into one index object: one JSONL line per result, streamed in order, verified as read. No bucket listing.');
  yield* index().opacity(1, 0.5);
  yield* indexed(SEEDS * 3, 4, easeInOutCubic);
  yield* waitFor(2);

  // 5. aggregate
  say('A single aggregate-only job reads the index and writes one summary per workflow.');
  yield* all(agg().opacity(1, 0.5), note().opacity(1, 0.5));
  yield* sequence(0.4, ...summaries.map(s => s().opacity(1, 0.5)));
  yield* waitFor(6);
});
