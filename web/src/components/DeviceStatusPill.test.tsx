import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DeviceStatusPill } from "@/components/DeviceStatusPill";
import { TooltipProvider } from "@/components/ui/tooltip";
import { createTestQueryClient } from "@/test/renderWithQueryClient";

const { getHealth, getDeviceStatus } = vi.hoisted(() => ({
  getHealth: vi.fn(),
  getDeviceStatus: vi.fn(),
}));
vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  getHealth,
  getDeviceStatus,
}));

function renderPill() {
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter>
        <TooltipProvider>
          {/* The pill is a link to the device page, so it needs a router. */}
          <DeviceStatusPill />
        </TooltipProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const pill = () => screen.getByTestId("device-status-pill");

describe("DeviceStatusPill", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getHealth.mockResolvedValue({ status: "ok", version: "0.1.0", database: "ok" });
    getDeviceStatus.mockResolvedValue({
      configured: false,
      connected: false,
      host: null,
      identity: null,
      last_status: null,
    });
  });

  it("reports the backend first: nothing else is knowable without it", async () => {
    getHealth.mockRejectedValue(new Error("Failed to fetch"));
    getDeviceStatus.mockResolvedValue({ configured: true, connected: true, host: "gaggimate" });
    renderPill();
    await waitFor(() => expect(pill()).toHaveAttribute("data-state", "bad"));
    expect(pill()).toHaveTextContent("Offline");
  });

  it("is not a fault when no machine is configured", async () => {
    renderPill();
    await waitFor(() => expect(pill()).toHaveAttribute("data-state", "good"));
    expect(pill()).toHaveTextContent("Online");
  });

  it("names the board when the machine is connected", async () => {
    getDeviceStatus.mockResolvedValue({
      configured: true,
      connected: true,
      host: "192.168.1.50",
      identity: { hardware: "GaggiMate Pro Rev 1.1", displayVersion: "v1.9.0" },
      last_status: null,
    });
    renderPill();
    await waitFor(() => expect(pill()).toHaveTextContent("GaggiMate Pro Rev 1.1"));
    expect(pill()).toHaveAttribute("data-state", "good");
  });

  it("says where it goes, since the sidebar no longer does", async () => {
    renderPill();
    await waitFor(() => expect(pill()).toHaveAttribute("data-state", "good"));
    // The device page has no nav entry; this pill is the way in, so the link
    // has to name its destination for anyone who cannot see a tooltip.
    expect(pill()).toHaveAttribute("href", "/device");
    expect(pill()).toHaveTextContent("Open the device page");
  });

  it("goes red when the machine is configured but not answering", async () => {
    getDeviceStatus.mockResolvedValue({
      configured: true,
      connected: false,
      host: "192.168.1.50",
      identity: null,
      last_status: null,
    });
    renderPill();
    await waitFor(() => expect(pill()).toHaveAttribute("data-state", "bad"));
    expect(pill()).toHaveTextContent("Machine offline");
  });
});
