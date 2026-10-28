import React from "react";
import Analytics from "./Analytics";

export default function BrokerageWorkspace() {
  return <div className="mx-auto w-full max-w-[1600px]">
    <Analytics reportMode="real-brokerage" active />
  </div>;
}
