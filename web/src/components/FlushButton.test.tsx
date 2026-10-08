import { screen, waitFor } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SettingsMap } from "@/api/types";
import { FlushButton } from "@/components/FlushButton";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const { getSettings, getDeviceStatus, startFlush } = vi.hoisted(() => ({
  getSettings: vi.fn(),
  getDeviceStatus: vi.fn(),
  startFlush: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getSettings,
  getDeviceStatus,
  startFlush,
}));
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

function settings(enabled: boolean): SettingsMap {
  return {
    deviceWritesEnabled: {
      key: "deviceWritesEnabled",
      type: "bool",
      secret: false,
      readonly: false,
      value: enabled,
      default: false,
      override: enabled ? true : null,
      source: enabled ? "database" : "default",
    },
  } as unknown as SettingsMap;
}

function status(connected: boolean) {
  return {
    configured: true,
    connected,
    host: "gaggimate.local",
    identity: null,
    last_status: null,
  };
}

const button = () => screen.queryByRole("button", { name: "Flush" });

describe("FlushButton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getDeviceStatus.mockResolvedValue(status(true));
    startFlush.mockResolvedValue({ started: true });
  });

  it("is not there while writes are off", async () => {
    getSettings.mockResolvedValue(settings(false));
    renderWithQueryClient(<FlushButton />);
    await waitFor(() => expect(getSettings).toHaveBeenCalled());
    await waitFor(() => expect(getDeviceStatus).toHaveBeenCalled());
    expect(button()).toBeNull();
  });

  it("with writes on, one click sends one flush and says so", async () => {
    getSettings.mockResolvedValue(settings(true));
    const user = setupUser();
    renderWithQueryClient(<FlushButton />);
    await waitFor(() => expect(button()).toBeEnabled());

    await user.click(button() as HTMLElement);

    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(startFlush).toHaveBeenCalledTimes(1);
  });

  it("a double click sends one flush", async () => {
    getSettings.mockResolvedValue(settings(true));
    let answer: (value: { started: boolean }) => void = () => {};
    startFlush.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    const user = setupUser();
    renderWithQueryClient(<FlushButton />);
    await waitFor(() => expect(button()).toBeEnabled());

    await user.dblClick(button() as HTMLElement);
    answer({ started: true });

    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(startFlush).toHaveBeenCalledTimes(1);
  });

  it("shows the server's refusal", async () => {
    getSettings.mockResolvedValue(settings(true));
    startFlush.mockRejectedValue(
      new Error("The machine is not in brew mode, so it was not flushed."),
    );
    const user = setupUser();
    renderWithQueryClient(<FlushButton />);
    await waitFor(() => expect(button()).toBeEnabled());

    await user.click(button() as HTMLElement);

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "The machine is not in brew mode, so it was not flushed.",
      ),
    );
  });

  it("is disabled while the machine is not connected", async () => {
    getSettings.mockResolvedValue(settings(true));
    getDeviceStatus.mockResolvedValue(status(false));
    renderWithQueryClient(<FlushButton />);
    await waitFor(() => expect(button()).toBeInTheDocument());
    await waitFor(() => expect(getDeviceStatus).toHaveBeenCalled());
    expect(button()).toBeDisabled();
  });

  it("is icon-only below lg, so the header fits at phone width and beside the sidebar", async () => {
    getSettings.mockResolvedValue(settings(true));
    renderWithQueryClient(<FlushButton />);
    await waitFor(() => expect(button()).toBeInTheDocument());
    expect(button()?.querySelector("span")).toHaveClass("max-lg:sr-only");
  });
});
