import { ResponsiveContainer } from "recharts";

/**
 * Recharts measures its parent on mount. Wait for the real size instead of
 * drawing every chart at a fixed desktop width during route transitions.
 * Throttle resize updates so animated panels do not redraw on every frame.
 */
export default function SafeResponsiveContainer({ children, ...props }) {
  return (
    <ResponsiveContainer
      width="100%"
      height="100%"
      minWidth={0}
      minHeight={1}
      debounce={48}
      {...props}
    >
      {children}
    </ResponsiveContainer>
  );
}
