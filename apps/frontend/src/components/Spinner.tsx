export function Spinner({ size = 24 }: { size?: number }) {
  return (
    <div
      className="rounded-full border-4 border-transparent animate-spin"
      style={{
        width: size,
        height: size,
        borderTopColor: '#628ff2',
      }}
    />
  );
}
