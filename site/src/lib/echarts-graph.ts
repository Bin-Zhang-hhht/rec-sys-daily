import * as echarts from "echarts/core";
import { GraphChart } from "echarts/charts";
import { AriaComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([GraphChart, TooltipComponent, AriaComponent, CanvasRenderer]);

export default echarts;
