import { Config } from "@remotion/cli/config";

Config.setRspack(true);
Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);
Config.setDelayRenderTimeoutInMilliseconds(240000); // ponytail: laptop is RAM-starved, headless browser start exceeds 30 s
