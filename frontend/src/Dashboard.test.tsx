import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Dashboard } from "./Dashboard";
import { envelope, failWith, ORG, stubApi, SUMMARY } from "./test-fixtures";

afterEach(() => vi.unstubAllGlobals());

const noop = () => undefined;

describe("Dashboard", () => {
  it("leads with the MOCK notice and shows the figures, charts and tables", async () => {
    stubApi();
    render(<Dashboard orgId={ORG} onChangeOrg={noop} />);

    const banner = (await screen.findByText("MOCK data")).closest(".banner") as HTMLElement;
    expect(banner).toHaveTextContent("MOCK data");
    expect(banner).toHaveTextContent("not live Amazon data");

    expect(await screen.findByText("₹1,23,456")).toBeInTheDocument(); // spend, Indian grouping
    expect(screen.getByText("23.4%")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Ad spend and ad sales/ })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /ACOS/ })).toBeInTheDocument();
    expect(screen.getByText("Target 30%")).toBeInTheDocument();
  });

  it("warns that the latest days are still maturing and leaves them out of comparisons", async () => {
    stubApi();
    render(<Dashboard orgId={ORG} onChangeOrg={noop} />);
    const notice = await screen.findByText(/still inside Amazon's attribution window/);
    expect(notice).toHaveTextContent("latest 3 days");
    expect(notice).toHaveTextContent("left out of every comparison");
    expect(screen.getByText(/changes compare/)).toHaveTextContent("25 days before");
  });

  it("shows campaign health with an icon and a label, and flags budget-capped campaigns", async () => {
    stubApi();
    render(<Dashboard orgId={ORG} onChangeOrg={noop} />);
    const campaigns = await screen.findByRole("region", { name: "Campaigns" });
    const row = within(campaigns).getByText("SP | Manual | steel water bottle").closest("tr") as HTMLElement;
    expect(within(row).getByText("Unhealthy")).toBeInTheDocument();
    expect(within(row).getByText("Budget-capped")).toBeInTheDocument();
    expect(within(row).getByText("50.0%")).toBeInTheDocument();
    const healthy = within(campaigns).getByText("SP | Auto | yoga mat").closest("tr") as HTMLElement;
    expect(within(healthy).getByText("Healthy")).toBeInTheDocument();
    expect(within(healthy).getByText("61% used")).toBeInTheDocument();
    // sorted by spend, largest first
    const names = within(campaigns).getAllByRole("rowheader").map((c) => c.textContent);
    expect(names.indexOf("SP | Manual | steel water bottle")).toBeLessThan(names.indexOf("SP | Auto | yoga mat"));
  });

  it("marks low-data keywords and shows an undefined ACOS as a dash", async () => {
    stubApi();
    render(<Dashboard orgId={ORG} onChangeOrg={noop} />);
    const low = (await screen.findByText("yoga mat 6mm")).closest("tr") as HTMLElement;
    expect(within(low).getByText(/low data/)).toBeInTheDocument();
    const noSales = screen.getByText("wireless earbuds premium").closest("tr") as HTMLElement;
    expect(within(noSales).getByText("—")).toBeInTheDocument(); // spend with no sales: undefined, not 0%
    expect(screen.getByText(/Showing 2 of 1,674/)).toBeInTheDocument();
  });

  it("shows a dash for an undefined headline KPI", async () => {
    stubApi({ "/dashboard/summary": envelope({ ...SUMMARY, kpis: { ...SUMMARY.kpis, acos_pct: null } }) });
    render(<Dashboard orgId={ORG} onChangeOrg={noop} />);
    const tile = (await screen.findByText("ACOS", { selector: ".tile-label" })).closest(".tile") as HTMLElement;
    expect(within(tile).getAllByText("—").length).toBeGreaterThan(0);
    expect(within(tile).queryByText("0.0%")).toBeNull();
  });

  it("sends the development tenant header on every analytics call", async () => {
    const calls = stubApi();
    render(<Dashboard orgId={ORG} onChangeOrg={noop} />);
    await screen.findByText("23.4%");
    const analytics = calls.urls.map((u, i) => ({ u, h: calls.headers[i] })).filter((c) => c.u.includes("/api/v1/"));
    expect(analytics).toHaveLength(4);
    for (const c of analytics) expect(c.h?.["X-Dev-Org-Id"]).toBe(ORG);
  });

  it("refetches with explicit dates when another range is chosen, derived from the latest data day", async () => {
    const calls = stubApi();
    render(<Dashboard orgId={ORG} onChangeOrg={noop} />);
    await screen.findByText("23.4%");
    expect(calls.urls.some((u) => u.includes("from="))).toBe(false); // the default window needs none
    fireEvent.click(screen.getByRole("button", { name: "Last 7 days" }));
    await waitFor(() => expect(calls.urls.some((u) => u.includes("from=2026-10-25&to=2026-10-31"))).toBe(true));
    expect(screen.getByRole("button", { name: "Last 7 days" })).toHaveAttribute("aria-pressed", "true");
  });

  it("disables the other ranges until the first response says which day is latest", () => {
    stubApi();
    render(<Dashboard orgId={ORG} onChangeOrg={noop} />);
    expect(screen.getByRole("button", { name: "Last 7 days" })).toBeDisabled();
  });

  it("explains a missing organization or data instead of showing zeros", async () => {
    const none = failWith(404, { title: "No advertising data" });
    stubApi({ "/dashboard/summary": none, "/dashboard/trends": none, "/campaigns": none, "/keywords": none });
    render(<Dashboard orgId={ORG} onChangeOrg={noop} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("seed-mock");
    expect(screen.queryByText("₹0")).toBeNull();
  });

  it("explains a rejected organization id", async () => {
    stubApi({ "/dashboard/summary": failWith(401, { title: "Invalid header" }) });
    render(<Dashboard orgId={ORG} onChangeOrg={noop} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("not accepted");
  });

  it("lets the user switch organization", async () => {
    stubApi();
    const change = vi.fn();
    render(<Dashboard orgId={ORG} onChangeOrg={change} />);
    await screen.findByText("23.4%");
    fireEvent.click(screen.getByRole("button", { name: "Change" }));
    expect(change).toHaveBeenCalledOnce();
  });

  it("does not claim data is MOCK when the source is live", async () => {
    stubApi({
      "/dashboard/summary": envelope(SUMMARY, {
        data_sources: ["AMAZON_ADS_API"],
        synthetic: false,
        notice: null,
        as_of: null,
        latest_date: "2026-10-31",
        mature_through: "2026-10-28",
      }),
    });
    render(<Dashboard orgId={ORG} onChangeOrg={noop} />);
    expect(await screen.findByText("Live data")).toBeInTheDocument();
    expect(screen.queryByText("MOCK data")).toBeNull();
  });
});
