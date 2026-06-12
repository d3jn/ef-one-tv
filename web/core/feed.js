/* Broadcast feed: one WebSocket to the server, auto-reconnecting, delivering each
 * snapshot to the supplied callback. Shared by every block — the block only sees
 * parsed snapshot objects and never touches the socket. */

export function startFeed(onState) {
  function connect() {
    const ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onmessage = (ev) => {
      try {
        onState(JSON.parse(ev.data));
      } catch (e) {
        console.error("bad payload", e);
      }
    };
    ws.onclose = () => setTimeout(connect, 1000); // retry until the server is back
    ws.onerror = () => ws.close();
  }
  connect();
}
