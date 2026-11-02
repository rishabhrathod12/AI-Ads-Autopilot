import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

afterEach(() => vi.unstubAllGlobals());

describe("App", () => {
  it("shows MOCK label and disabled execution from /health", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          status: "ok",
          ads_provider: "mock",
          data_source: "MOCK",
          execution_enabled: false,
        }),
      }),
    );
    render(<App />);
    expect(await screen.findByText("MOCK")).toBeInTheDocument();
    expect(screen.getByText(/not live Amazon data/)).toBeInTheDocument();
    expect(screen.getByText(/Ad execution: disabled/)).toBeInTheDocument();
  });

  it("shows an error when the backend is down", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("network down");
  });
});
