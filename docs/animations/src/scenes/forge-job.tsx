import {Circle, Rect, Txt, makeScene2D} from '@motion-canvas/2d';
import {all, createRef, waitFor} from '@motion-canvas/core';
import {C, box} from './forge-kit';

export default makeScene2D(function* (view) {
  view.fill(C.bg);

  const submit = createRef<Rect>();
  const intake = createRef<Rect>();
  const admission = createRef<Rect>();
  const workflow = createRef<Rect>();
  const place = createRef<Rect>();
  const status = createRef<Rect>();
  const statusTxt = createRef<Txt>();
  const caption = createRef<Txt>();
  const log = createRef<Txt>();
  const token = createRef<Circle>();
  const heading = createRef<Txt>();

  const taskIds = ['build', 'sim', 'agg'];
  const taskType = ['compile', 'simulate', 'aggregate'];
  const taskColor = [C.violet, C.blue, C.amber];
  const taskX = [-450, 0, 450];
  const tasks = taskIds.map(() => createRef<Rect>());
  const pools = taskIds.map(() => createRef<Rect>());
  const workers = taskIds.map(() => createRef<Rect>());
  const buckets = taskIds.map(() => createRef<Rect>());

  const say = (t: string) => caption().text(t);
  const logLine = (t: string) => log().text(t);
  const setStatus = (s: string, c: string) => {
    statusTxt().text(s);
    status().stroke(c);
    statusTxt().fill(c);
  };

  view.add(
    <>
      <Txt
        ref={heading}
        text="wasm-dag-forge: one job, end to end"
        fill={C.text}
        fontSize={44}
        fontWeight={700}
        x={-270}
        y={-470}
      />
      <Txt
        text="solid = has code/contract   dashed = designed only"
        fill={C.dim}
        fontSize={22}
        x={640}
        y={-470}
      />
      <Rect
        ref={status}
        x={-800}
        y={-400}
        width={260}
        height={50}
        stroke={C.dim}
        lineWidth={3}
        radius={25}
        opacity={0}
      >
        <Txt ref={statusTxt} text="status: -" fill={C.dim} fontSize={26} />
      </Rect>
      {box({ref: submit, x: -700, y: -300, w: 340, h: 130, title: 'Submit API', sub: 'validate, compile plan, dedup row', designed: true})}
      {box({ref: intake, x: -235, y: -300, w: 340, h: 130, title: 'RabbitMQ intake', sub: 'queue per (tenant, class)', color: C.amber, designed: true})}
      {box({ref: admission, x: 235, y: -300, w: 340, h: 130, title: 'Admission (layer 1)', sub: 'priority, quota, class cap', color: C.pink, designed: true})}
      {box({ref: workflow, x: 700, y: -300, w: 340, h: 130, title: 'Temporal workflow', sub: 'one job = one workflow', color: C.green, designed: true})}
      {box({ref: place, x: -800, y: -110, w: 240, h: 110, title: 'Placement', sub: 'layer 2 pool pick', color: C.pink, designed: true})}
      {taskIds.map((id, i) =>
        box({ref: tasks[i], x: taskX[i], y: -110, w: 300, h: 110, title: `${id}`, sub: `type: ${taskType[i]}`, color: taskColor[i]}),
      )}
      {taskIds.map((_, i) =>
        box({ref: pools[i], x: taskX[i], y: 70, w: 300, h: 90, title: `pool: ${taskType[i]}`, sub: 'Temporal task queue', color: taskColor[i], designed: true}),
      )}
      {taskIds.map((_, i) =>
        box({
          ref: workers[i],
          x: taskX[i],
          y: 230,
          w: 300,
          h: 100,
          title: i === 1 ? 'slot pod (WASM)' : 'slot pod (native)',
          sub: i === 1 ? 'fresh instance, seed=42' : i === 0 ? 'builds component' : 'reads results via index',
          color: taskColor[i],
          designed: true,
        }),
      )}
      {box({ref: buckets[0], x: -450, y: 390, w: 300, h: 70, title: 'wdf-components', color: C.violet})}
      {box({ref: buckets[1], x: 0, y: 390, w: 300, h: 70, title: 'wdf-results', color: C.blue})}
      {box({ref: buckets[2], x: 450, y: 390, w: 300, h: 70, title: 'summary', color: C.amber})}
      <Txt
        ref={log}
        text=""
        fill={C.green}
        fontSize={24}
        fontFamily="monospace"
        x={300}
        y={-400}
      />
      <Txt
        ref={caption}
        text=""
        fill={C.text}
        fontSize={28}
        x={0}
        y={495}
        width={1700}
        textWrap
        textAlign="center"
      />
      <Circle ref={token} width={26} height={26} fill={C.green} opacity={0} x={-700} y={-300} />
    </>,
  );

  const show = (n: Rect) => n.opacity(1, 0.4);

  yield* waitFor(2);
  yield* show(status());
  setStatus('status: -', C.dim);

  // 1. submit
  yield* show(submit());
  say('1. The job document is validated once per template, compiled to a plan, and written as an accepted_job row. A unique (tenant, submission_key) dedups.');
  token().opacity(1);
  yield* waitFor(2.5);
  setStatus('status: accepted', C.amber);
  logLine('dev.wdf.job.accepted.v1');
  yield* waitFor(1.5);

  // 2. intake
  yield* show(intake());
  say('2. A pointer-only event (manifest ref, batch id, submission key) goes to the tenant intake queue. Bad messages go to the DLQ.');
  yield* token().position([-235, -300], 1.2);
  yield* waitFor(2.5);

  // 3. admission
  yield* show(admission());
  say('3. Layer 1 admission gates by priority, quota and class cap, then starts a workflow whose id derives from (tenant, key). A duplicate id is dropped.');
  yield* token().position([235, -300], 1.2);
  setStatus('status: admitted', C.pink);
  yield* waitFor(2.5);

  // 4. workflow
  yield* show(workflow());
  say('4. One Temporal workflow owns the job and its three tasks. A task is an activity inside the workflow: workflow and task are not synonyms.');
  yield* token().position([700, -300], 1.2);
  setStatus('status: running', C.green);
  logLine('job.started');
  yield* waitFor(3);

  // 5. dag
  say('5. The plan is a DAG. Data edges are control edges: sim needs build.component, agg needs sim.result.');
  yield* all(show(place()), ...tasks.map(t => t().opacity(1, 0.5)));
  yield* waitFor(3);

  const notes = [
    'build (compile): first run is a cache miss, so it runs on the native compile pool and writes the component.',
    'sim (simulate): placed on the simulate pool. A fresh WASM instance runs with the job seed and writes a result.',
    'agg (aggregate): a native task reads results through an index object, never by listing a bucket.',
  ];
  const outs = [
    'component stored in wdf-components',
    'result stored in wdf-results',
    'summary stored',
  ];
  for (let i = 0; i < 3; i++) {
    say(notes[i]);
    yield* tasks[i]().stroke('#fff', 0.3);
    logLine(`task.placed  ${taskIds[i]} -> pool=${taskType[i]}`);
    yield* all(pools[i]().opacity(1, 0.4), token().position([taskX[i], -110], 0.6));
    yield* token().position([taskX[i], 70], 0.7);
    yield* workers[i]().opacity(1, 0.4);
    yield* token().position([taskX[i], 230], 0.7);
    yield* waitFor(1.5);
    yield* buckets[i]().opacity(1, 0.4);
    yield* token().position([taskX[i], 390], 0.7);
    logLine(outs[i]);
    yield* all(
      tasks[i]().stroke(C.green, 0.4),
      token().position([taskX[i], -110], 0.9),
    );
    yield* waitFor(1.5);
  }

  token().opacity(0);
  setStatus('status: succeeded', C.green);
  logLine('job.succeeded');
  say('Only the accepted attempt is committed, every transition is an append-only event, and the job row ends in a terminal status.');
  yield* waitFor(5);
});
