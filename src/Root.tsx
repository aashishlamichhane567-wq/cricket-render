import React from "react";
import { Composition } from "remotion";
import { ClipShort, Props } from "./ClipShort";

// Every real Short is rendered with --props=out/<id>/props.json, written by build.py.
const empty: Props = { durationInFrames: 30, frames: "", srcW: 1920, srcH: 1080, segments: [], beats: [], captions: [] };

export const RemotionRoot: React.FC = () => (
  <Composition
    id="ClipShort"
    component={ClipShort}
    fps={30}
    width={1080}
    height={1920}
    defaultProps={empty}
    calculateMetadata={({ props }) => ({ durationInFrames: props.durationInFrames })}
  />
);
