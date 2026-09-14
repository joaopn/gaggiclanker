import { screen } from "@testing-library/react";
import { Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DeviceStatusData } from "@/api/types";
import { DevicePage } from "@/pages/DevicePage";
import { renderWithQueryClient } from "@/test/renderWithQueryClient";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  Toaster: () => null,
}));

const { getDeviceStatus, getSyncStatus, getDeviceWrites, getCleanupPlan, getPendingNotes } =
  vi.hoisted(() => ({
    getDeviceStatus: vi.fn(),
    getSyncStatus: vi.fn(),
    getDeviceWrites: vi.fn(),
    getCleanupPlan: vi.fn(),
    getPendingNotes: vi.fn(),
  }));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getDeviceStatus,
  getSyncStatus,
  getDeviceWrites,
  getCleanupPlan,
  getPendingNotes,
}));

function deviceStatus(overrides: Partial<DeviceStatusData> = {}): DeviceStatusData {
  return {
    configured: true,
    connected: true,
    host: "gaggimate.local",
    identity: {
      hardware: "GaggiMate Pro",
      displayVersion: "1.8.2",
      controllerVersion: "1.8.2",
      latestVersion: "1.8.3",
      channel: "latest",
      updating: false,
      spiffsTotal: 1_048_576,
      spiffsUsed: 786_432,
      sdTotal: 0,
    },
    last_status: null,
    ...overrides,
  };
}

/** Where a redirect landed, including the anchor. */
function Landed() {
  const { pathname, hash } = useLocation();
  return <p data-testid="landed">{`${pathname}${hash}`}</p>;
}

function renderAt(path: string) {
  return renderWithQueryClient(
    <Routes>
      <Route path="/device" element={<DevicePage />} />
      <Route path="/sync" element={<Landed />} />
    </Routes>,
    { initialEntries: [path] },
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getDeviceStatus.mockResolvedValue(deviceStatus());
});

describe("DevicePage", () => {
  it("names the board, the versions and the connection", async () => {
    renderAt("/device");

    expect(await screen.findByRole("heading", { name: "GaggiMate Pro" })).toBeInTheDocument();
    expect(screen.getByText("gaggimate.local · connected")).toBeInTheDocument();
    expect(screen.getByTestId("device-versions")).toHaveTextContent("1.8.3");
    expect(screen.getByTestId("device-connection")).toHaveTextContent("connected");
  });

  it("holds no sync, notes, storage or write-audit card: those are on the Sync page", async () => {
    renderAt("/device");
    await screen.findByRole("heading", { name: "GaggiMate Pro" });

    expect(screen.queryByTestId("sync-runs")).not.toBeInTheDocument();
    expect(screen.queryByTestId("notes-pending")).not.toBeInTheDocument();
    expect(screen.queryByTestId("cleanup-summary")).not.toBeInTheDocument();
    expect(screen.queryByTestId("device-writes-empty")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sync now" })).not.toBeInTheDocument();
    // Nothing on this page asks for any of it either.
    expect(getSyncStatus).not.toHaveBeenCalled();
    expect(getDeviceWrites).not.toHaveBeenCalled();
    expect(getCleanupPlan).not.toHaveBeenCalled();
    expect(getPendingNotes).not.toHaveBeenCalled();

    expect(screen.getByRole("link", { name: /Sync with the machine/ })).toHaveAttribute(
      "href",
      "/sync",
    );
  });

  it("has no telemetry card, because the machine's own UI has one", async () => {
    renderAt("/device");
    await screen.findByRole("heading", { name: "GaggiMate Pro" });

    expect(screen.queryByTestId("device-telemetry")).not.toBeInTheDocument();
    expect(screen.queryByTestId("device-warnings")).not.toBeInTheDocument();
  });

  it.each([
    ["#storage", "/sync#storage"],
    ["#cleanup", "/sync#storage"],
    ["#notes", "/sync#notes"],
    ["#sync", "/sync#pull"],
    ["#writes", "/sync#writes"],
  ])("sends an old %s anchor to the section that moved", async (anchor, target) => {
    renderAt(`/device${anchor}`);
    expect(await screen.findByTestId("landed")).toHaveTextContent(target);
  });

  it("treats 'no machine configured' as a setup step, not a fault", async () => {
    getDeviceStatus.mockResolvedValue(
      deviceStatus({ configured: false, connected: false, host: null, identity: null }),
    );

    renderAt("/device");

    expect(await screen.findByText("No machine configured")).toBeInTheDocument();
    expect(screen.getByText(/gaggimateHost/)).toBeInTheDocument();
  });
});
