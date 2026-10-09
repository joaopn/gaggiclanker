/**
 * Where the person was on the Chat page, remembered in this browser.
 *
 * The open conversation lives in the URL (`?thread=`), and the sidebar's Chat
 * link is a bare `/chat`, so clicking away and back dropped it and landed on
 * the newest Set's badge with nothing open. The page saves the open
 * conversation and the badge the person picked here, and reads them back only
 * when `/chat` comes with no link of its own: a Discuss button or a copied URL
 * still says where to go. In `localStorage` like the Shots page's view
 * preferences; every access is guarded, so a private window or blocked site
 * data only means the page opens as it did before.
 */
export const CHAT_POSITION_KEY = "chat.position.v1";

export type ChatPosition = {
  /** The open conversation, or null when none was. */
  thread: number | null;
  /** The badge the person opened (`folderKey`), or null when they left the page's own choice. */
  folder: string | null;
};

const NOWHERE: ChatPosition = { thread: null, folder: null };

export function loadChatPosition(storage: Storage | undefined = safeStorage()): ChatPosition {
  let stored: unknown;
  try {
    stored = JSON.parse(storage?.getItem(CHAT_POSITION_KEY) ?? "null");
  } catch {
    return NOWHERE;
  }
  if (typeof stored !== "object" || stored === null) return NOWHERE;
  const { thread, folder } = stored as Record<string, unknown>;
  // Each field falls back on its own: a bad thread id still leaves the badge.
  return {
    thread: Number.isSafeInteger(thread) && (thread as number) > 0 ? (thread as number) : null,
    folder: typeof folder === "string" && folder !== "" ? folder : null,
  };
}

export function saveChatPosition(
  position: ChatPosition,
  storage: Storage | undefined = safeStorage(),
): void {
  try {
    storage?.setItem(CHAT_POSITION_KEY, JSON.stringify(position));
  } catch {
    // A place to come back to is not worth a toast.
  }
}

/** `localStorage`, or nothing — reading the property itself can throw. */
function safeStorage(): Storage | undefined {
  try {
    return window.localStorage;
  } catch {
    return undefined;
  }
}
