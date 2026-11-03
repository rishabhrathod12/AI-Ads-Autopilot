import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LineChart, type Series } from "./LineChart";

const dates = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31"];
const money = (v: number) => `₹${v}`;

function series(values: (number | null)[], key = "a", label = "Spend"): Series {
  return { key, label, color: "var(--series-1)", values, format: money };
}

function chart(props: Partial<React.ComponentProps<typeof LineChart>> = {}) {
  return render(
    <LineChart
      title="Spend and sales"
      subtitle="Per day"
      dates={dates}
      matureThrough={2}
      axisFormat={(v) => `${v}`}
      series={[series([10, 20, 30, 40, 50, 60])]}
      {...props}
    />,
  );
}

describe("LineChart", () => {
  it("draws the maturing days dashed, separately from the complete days", () => {
    chart();
    const dashed = screen.getByTestId("maturing-a");
    expect(dashed).toHaveAttribute("stroke-dasharray");
    expect(dashed).toHaveAttribute("opacity", "0.7");
  });

  it("draws no dashed segment when every day is complete", () => {
    chart({ matureThrough: 5 });
    expect(screen.queryByTestId("maturing-a")).toBeNull();
  });

  it("shows a legend for two series but not for one", () => {
    const { unmount } = chart();
    expect(screen.queryByLabelText("Legend")).toBeNull();
    unmount();
    chart({ series: [series([1, 2, 3, 4, 5, 6]), series([2, 3, 4, 5, 6, 7], "b", "Sales")] });
    const legend = screen.getByLabelText("Legend");
    expect(within(legend).getByText("Spend")).toBeInTheDocument();
    expect(within(legend).getByText("Sales")).toBeInTheDocument();
  });

  it("is operable from the keyboard and the tooltip lists every series with values first", () => {
    chart({ series: [series([10, 20, 30, 40, 50, 60]), series([5, 6, 7, 8, 9, 10], "b", "Sales")] });
    const svg = screen.getByRole("img");
    fireEvent.focus(svg); // lands on the latest day
    expect(screen.getByRole("status")).toHaveTextContent("31 Oct");
    expect(screen.getByRole("status")).toHaveTextContent("still maturing");
    expect(screen.getByRole("status")).toHaveTextContent("₹60");
    expect(screen.getByRole("status")).toHaveTextContent("₹10");
    fireEvent.keyDown(svg, { key: "Home" });
    expect(screen.getByRole("status")).toHaveTextContent("26 Oct");
    expect(screen.getByRole("status")).not.toHaveTextContent("still maturing");
    fireEvent.keyDown(svg, { key: "ArrowRight" });
    expect(screen.getByRole("status")).toHaveTextContent("27 Oct");
    fireEvent.keyDown(svg, { key: "End" });
    fireEvent.keyDown(svg, { key: "ArrowRight" }); // clamps at the last day
    expect(screen.getByRole("status")).toHaveTextContent("31 Oct");
    fireEvent.blur(svg);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows an undefined day as a dash in the tooltip and the table, never zero", () => {
    chart({ series: [series([10, 20, null, 40, 50, 60], "acos", "ACOS")] });
    const svg = screen.getByRole("img");
    fireEvent.focus(svg);
    fireEvent.keyDown(svg, { key: "Home" });
    fireEvent.keyDown(svg, { key: "ArrowRight" });
    fireEvent.keyDown(svg, { key: "ArrowRight" });
    expect(screen.getByRole("status")).toHaveTextContent("28 Oct");
    expect(screen.getByRole("status")).toHaveTextContent("—");
    const row = screen.getByRole("row", { name: /28 Oct/ });
    expect(within(row).getByText("—")).toBeInTheDocument();
  });

  it("offers a table view with one row per day and marks maturing days", () => {
    chart();
    expect(screen.getByText("Table view")).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(dates.length + 1);
    expect(screen.getAllByText(/\(maturing\)/)).toHaveLength(3);
  });

  it("has an accessible name that says what it shows", () => {
    chart();
    expect(screen.getByRole("img")).toHaveAccessibleName(/Spend and sales.*26 Oct to 31 Oct.*arrow keys/);
  });

  it("draws a labelled reference line", () => {
    chart({ reference: { value: 35, label: "Target 35%" } });
    expect(screen.getByText("Target 35%")).toBeInTheDocument();
  });

  it("renders nothing for an empty series", () => {
    const { container } = chart({ dates: [], series: [series([])] });
    expect(container).toBeEmptyDOMElement();
  });
});
