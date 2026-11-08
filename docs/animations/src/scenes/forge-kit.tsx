import {Rect, Txt} from '@motion-canvas/2d';
import {Reference} from '@motion-canvas/core';

export const C = {
  bg: '#0f1220',
  panel: '#1a1f36',
  line: '#3a4170',
  text: '#e8ecff',
  dim: '#8b93c4',
  blue: '#5b8cff',
  green: '#3ddc97',
  amber: '#ffb454',
  pink: '#ff6b9d',
  violet: '#a78bfa',
};

export const fmt = (n: number) => Math.round(n).toLocaleString('en-US');

interface BoxProps {
  ref: Reference<Rect>;
  x: number;
  y: number;
  w: number;
  h: number;
  title: string;
  sub?: string;
  color?: string;
  // Dashed outline marks a component that is designed but has no code yet.
  designed?: boolean;
}

export function box({
  ref,
  x,
  y,
  w,
  h,
  title,
  sub,
  color = C.blue,
  designed = false,
}: BoxProps) {
  return (
    <Rect
      ref={ref}
      x={x}
      y={y}
      width={w}
      height={h}
      fill={C.panel}
      stroke={color}
      lineWidth={3}
      lineDash={designed ? [12, 8] : []}
      radius={14}
      opacity={0}
      layout
      direction="column"
      alignItems="center"
      justifyContent="center"
      gap={6}
      padding={12}
    >
      <Txt text={title} fill={C.text} fontSize={28} fontWeight={700} />
      {sub ? (
        <Txt
          text={sub}
          fill={C.dim}
          fontSize={20}
          textAlign="center"
          textWrap
          width={w - 32}
        />
      ) : null}
    </Rect>
  );
}
