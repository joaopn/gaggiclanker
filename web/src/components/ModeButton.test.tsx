import { screen, waitFor } from "@testing-library/react";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SettingsMap } from "@/api/types";
import { ModeButton } from "@/components/ModeButton";
import { renderWithQueryClient, setupUser } from "@/test/renderWithQueryClient";

const { getSettings, getDeviceStatus, changeMode } = vi.hoisted(() => ({
  getSettings: vi.fn(),
  getDeviceStatus: vi.fn(),
  changeMode: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getSettings,
  getDeviceStatus,
  changeMode,
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

function status(mode: number | null, connected = true) {
  return {
    configured: true,
    connected,
    host: "gaggimate.local",
    identity: null,
    last_status: mode === null ? null : { m: mode },
  };
}

const button = () => screen.queryByTestId("mode-button");

describe("ModeButton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getSettings.mockResolvedValue(settings(true));
    getDeviceStatus.mockResolvedValue(status(1));
    changeMode.mockImplementation(async (mode: string) => ({ mode }));
  });

  it("is not there while writes are off", async () => {
    getSettings.mockResolvedValue(settings(false));
    renderWithQueryClient(<ModeButton />);
    await waitFor(() => expect(getSettings).toHaveBeenCalled());
    await waitFor(() => expect(getDeviceStatus).toHaveBeenCalled());
    expect(button()).toBeNull();
  });

  it("in brew mode it offers standby, and one click sends it", async () => {
    const user = setupUser();
    renderWithQueryClient(<ModeButton />);
    const offered = await screen.findByRole("button", { name: "Switch to Standby" });
    await waitFor(() => expect(offered).toBeEnabled());

    await user.click(offered);

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("The machine is in standby."));
    expect(changeMode).toHaveBeenCalledTimes(1);
    expect(changeMode).toHaveBeenCalledWith("standby");
  });

  it.each([
    [0, "standby"],
    [2, "steam"],
    [3, "water"],
    [4, "grind"],
  ])("in mode %i (%s) it offers brew", async (mode) => {
    getDeviceStatus.mockResolvedValue(status(mode));
    const user = setupUser();
    renderWithQueryClient(<ModeButton />);
    const offered = await screen.findByRole("button", { name: "Switch to Brew" });
    await waitFor(() => expect(offered).toBeEnabled());

    await user.click(offered);

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("The machine is in brew mode."));
    expect(changeMode).toHaveBeenCalledWith("brew");
  });

  it("reads the device status again once the machine answered, so the label follows", async () => {
    const user = setupUser();
    renderWithQueryClient(<ModeButton />);
    const offered = await screen.findByRole("button", { name: "Switch to Standby" });
    await waitFor(() => expect(offered).toBeEnabled());
    getDeviceStatus.mockResolvedValue(status(0));

    await user.click(offered);

    expect(await screen.findByRole("button", { name: "Switch to Brew" })).toBeEnabled();
  });

  it("a double click sends one switch", async () => {
    let answer: (value: { mode: string }) => void = () => {};
    changeMode.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    const user = setupUser();
    renderWithQueryClient(<ModeButton />);
    const offered = await screen.findByRole("button", { name: "Switch to Standby" });
    await waitFor(() => expect(offered).toBeEnabled());

    await user.dblClick(offered);
    answer({ mode: "standby" });

    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(changeMode).toHaveBeenCalledTimes(1);
  });

  it("shows the server's refusal", async () => {
    changeMode.mockRejectedValue(
      new Error("The machine is running a shot or a flush, and switching mode would stop it."),
    );
    const user = setupUser();
    renderWithQueryClient(<ModeButton />);
    const offered = await screen.findByRole("button", { name: "Switch to Standby" });
    await waitFor(() => expect(offered).toBeEnabled());

    await user.click(offered);

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "The machine is running a shot or a flush, and switching mode would stop it.",
      ),
    );
  });

  it("is disabled while the machine is not connected", async () => {
    getDeviceStatus.mockResolvedValue(status(1, false));
    renderWithQueryClient(<ModeButton />);
    await waitFor(() => expect(button()).toBeInTheDocument());
    await waitFor(() => expect(getDeviceStatus).toHaveBeenCalled());
    expect(button()).toBeDisabled();
  });

  it("is disabled while the machine has not said which mode it is in", async () => {
    getDeviceStatus.mockResolvedValue(status(null));
    renderWithQueryClient(<ModeButton />);
    await waitFor(() => expect(button()).toBeInTheDocument());
    await waitFor(() => expect(getDeviceStatus).toHaveBeenCalled());
    expect(button()).toBeDisabled();
    expect(button()).toHaveAccessibleName("Switch to Brew");
  });

  it("is icon-only below lg, so the header fits at phone width and beside the sidebar", async () => {
    renderWithQueryClient(<ModeButton />);
    await waitFor(() => expect(button()).toBeInTheDocument());
    expect(button()?.querySelector("span")).toHaveClass("max-lg:sr-only");
  });
});
