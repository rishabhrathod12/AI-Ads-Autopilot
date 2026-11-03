import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { ORG, stubApi } from "./test-fixtures";

beforeEach(() => window.localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("App", () => {
  it("asks for a development organization id first, and rejects malformed ones", async () => {
    stubApi();
    render(<App />);
    const input = screen.getByLabelText("Organization id");
    const open = screen.getByRole("button", { name: "Open dashboard" });
    expect(open).toBeDisabled();
    fireEvent.change(input, { target: { value: "not-a-uuid" } });
    expect(screen.getByText("That is not a valid id.")).toBeInTheDocument();
    expect(open).toBeDisabled();
    fireEvent.change(input, { target: { value: ORG } });
    expect(open).toBeEnabled();
    expect(screen.getByText(/Development only/)).toBeInTheDocument();
  });

  it("remembers the organization and opens the dashboard", async () => {
    stubApi();
    render(<App />);
    fireEvent.change(screen.getByLabelText("Organization id"), { target: { value: ORG.toUpperCase() } });
    fireEvent.click(screen.getByRole("button", { name: "Open dashboard" }));
    expect(await screen.findByText("23.4%")).toBeInTheDocument();
    expect(window.localStorage.getItem("adpilot.devOrgId")).toBe(ORG);
  });

  it("goes straight to the dashboard when an organization is already stored", async () => {
    window.localStorage.setItem("adpilot.devOrgId", ORG);
    stubApi();
    render(<App />);
    expect(await screen.findByText("23.4%")).toBeInTheDocument();
    expect(screen.queryByLabelText("Organization id")).toBeNull();
  });

  it("forgets the organization when asked to change it", async () => {
    window.localStorage.setItem("adpilot.devOrgId", ORG);
    stubApi();
    render(<App />);
    await screen.findByText("23.4%");
    fireEvent.click(screen.getByRole("button", { name: "Change" }));
    expect(screen.getByLabelText("Organization id")).toBeInTheDocument();
    expect(window.localStorage.getItem("adpilot.devOrgId")).toBeNull();
  });

  it("still works when browser storage is unavailable", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    stubApi();
    render(<App />);
    fireEvent.change(screen.getByLabelText("Organization id"), { target: { value: ORG } });
    fireEvent.click(screen.getByRole("button", { name: "Open dashboard" }));
    expect(await screen.findByText("23.4%")).toBeInTheDocument();
    vi.restoreAllMocks();
  });

  it("always shows the backend status: MOCK data and ad execution disabled", async () => {
    stubApi();
    render(<App />);
    expect(await screen.findByText("MOCK")).toBeInTheDocument();
    expect(screen.getByText(/not live Amazon data/)).toBeInTheDocument();
    expect(screen.getByText(/Ad execution: disabled/)).toBeInTheDocument();
  });

  it("reports an unreachable backend", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("network down");
  });
});
