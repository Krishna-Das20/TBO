import type { NextConfig } from "next";

const config: NextConfig = {
  output: "standalone", // small container image for Cloud Run
  poweredByHeader: false,
};

export default config;
