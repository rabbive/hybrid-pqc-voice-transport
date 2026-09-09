// hpqv Track 3 secondary model: hybrid vs TCP/TLS vs QUIC transport over a
// lossy/delayed point-to-point link. ns-3 has no official QUIC module, so the
// "quic" scheme models its 1-RTT handshake as a single UDP burst rather than
// a real QUIC handshake -- a proxy, not a protocol implementation.
#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/flow-monitor-module.h"
#include "ns3/internet-module.h"
#include "ns3/network-module.h"
#include "ns3/point-to-point-module.h"

#include <iomanip>
#include <iostream>

using namespace ns3;

namespace {

std::string g_scheme;
double g_hsMs = 0.0;
double g_dur = 0.0;
uint32_t g_hsBytes = 0;
double g_ttfbMs = -1.0;
bool g_hsDone = false;
Ptr<PacketSink> g_hsSink;
Ptr<Node> g_voiceSrcNode;
Address g_voiceSinkAddr;

void
StartVoice ()
{
  std::string proto = (g_scheme == "tcp") ? "ns3::TcpSocketFactory" : "ns3::UdpSocketFactory";
  OnOffHelper onoff (proto, g_voiceSinkAddr);
  onoff.SetAttribute ("DataRate", DataRateValue (DataRate ("64kbps")));
  onoff.SetAttribute ("PacketSize", UintegerValue (160));
  onoff.SetAttribute ("OnTime", StringValue ("ns3::ConstantRandomVariable[Constant=1e9]"));
  onoff.SetAttribute ("OffTime", StringValue ("ns3::ConstantRandomVariable[Constant=0]"));

  ApplicationContainer apps = onoff.Install (g_voiceSrcNode);
  Ptr<Application> app = apps.Get (0);
  // Installed mid-simulation: relative StartTime/StopTime schedule from
  // "now" (the handshake completion time), not from t=0.
  app->SetStartTime (Seconds (0.0));
  app->SetStopTime (Seconds (g_dur));
  app->Initialize ();
}

void
HandshakeRxCallback (Ptr<const Packet>, const Address &)
{
  if (g_hsDone || g_hsSink->GetTotalRx () < g_hsBytes)
    {
      return;
    }
  g_hsDone = true;
  g_ttfbMs = Simulator::Now ().GetSeconds () * 1000.0 + g_hsMs;
  StartVoice ();
}

} // namespace

int
main (int argc, char *argv[])
{
  g_scheme = "hybrid";
  double loss = 0.0;
  double delayMs = 50.0;
  g_hsBytes = 10842;
  g_hsMs = 1.0;
  g_dur = 2.0;

  CommandLine cmd;
  cmd.AddValue ("scheme", "hybrid|tcp|quic", g_scheme);
  cmd.AddValue ("loss", "packet loss rate [0,1]", loss);
  cmd.AddValue ("delayMs", "link RTT in ms", delayMs);
  cmd.AddValue ("hsBytes", "handshake payload size in bytes", g_hsBytes);
  cmd.AddValue ("hsMs", "fixed handshake CPU cost in ms", g_hsMs);
  cmd.AddValue ("dur", "voice phase duration in seconds", g_dur);
  cmd.Parse (argc, argv);

  NodeContainer nodes;
  nodes.Create (2);

  PointToPointHelper p2p;
  p2p.SetDeviceAttribute ("DataRate", StringValue ("10Mbps"));
  p2p.SetChannelAttribute ("Delay", TimeValue (MilliSeconds (delayMs / 2.0)));
  NetDeviceContainer devices = p2p.Install (nodes);

  Ptr<RateErrorModel> errorModel = CreateObject<RateErrorModel> ();
  errorModel->SetAttribute ("ErrorUnit", EnumValue (RateErrorModel::ERROR_UNIT_PACKET));
  errorModel->SetAttribute ("ErrorRate", DoubleValue (loss));
  devices.Get (1)->SetAttribute ("ReceiveErrorModel", PointerValue (errorModel));

  InternetStackHelper stack;
  stack.Install (nodes);

  Ipv4AddressHelper address;
  address.SetBase ("10.1.1.0", "255.255.255.0");
  Ipv4InterfaceContainer ifaces = address.Assign (devices);

  uint16_t hsPort = 5000;
  uint16_t voicePort = 5001;

  bool hsIsTcp = (g_scheme == "tcp" || g_scheme == "hybrid");
  std::string hsProto = hsIsTcp ? "ns3::TcpSocketFactory" : "ns3::UdpSocketFactory";
  std::string voiceProto = (g_scheme == "tcp") ? "ns3::TcpSocketFactory" : "ns3::UdpSocketFactory";

  Address hsSinkAddr (InetSocketAddress (ifaces.GetAddress (1), hsPort));
  ApplicationContainer hsSenderApp;
  if (hsIsTcp)
    {
      BulkSendHelper bulk (hsProto, hsSinkAddr);
      bulk.SetAttribute ("MaxBytes", UintegerValue (g_hsBytes));
      hsSenderApp = bulk.Install (nodes.Get (0));
    }
  else
    {
      // BulkSendHelper requires SOCK_STREAM, so the UDP-handshake (quic)
      // scheme sends via OnOff instead. Left unbounded (no MaxBytes) so the
      // stream keeps emitting datagrams past hsBytes under loss -- a proxy
      // for QUIC's own handshake-packet retransmission, not a literal
      // single burst.
      OnOffHelper onoffHs (hsProto, hsSinkAddr);
      onoffHs.SetAttribute ("DataRate", DataRateValue (DataRate ("100Mbps")));
      onoffHs.SetAttribute ("PacketSize", UintegerValue (1400));
      onoffHs.SetAttribute ("OnTime", StringValue ("ns3::ConstantRandomVariable[Constant=1e9]"));
      onoffHs.SetAttribute ("OffTime", StringValue ("ns3::ConstantRandomVariable[Constant=0]"));
      hsSenderApp = onoffHs.Install (nodes.Get (0));
    }
  hsSenderApp.Start (Seconds (0.0));

  PacketSinkHelper hsSinkHelper (hsProto, InetSocketAddress (Ipv4Address::GetAny (), hsPort));
  ApplicationContainer hsSinkApps = hsSinkHelper.Install (nodes.Get (1));
  hsSinkApps.Start (Seconds (0.0));
  g_hsSink = DynamicCast<PacketSink> (hsSinkApps.Get (0));
  g_hsSink->TraceConnectWithoutContext ("Rx", MakeCallback (&HandshakeRxCallback));

  g_voiceSrcNode = nodes.Get (0);
  g_voiceSinkAddr = InetSocketAddress (ifaces.GetAddress (1), voicePort);
  PacketSinkHelper voiceSinkHelper (voiceProto, InetSocketAddress (Ipv4Address::GetAny (), voicePort));
  ApplicationContainer voiceSinkApps = voiceSinkHelper.Install (nodes.Get (1));
  voiceSinkApps.Start (Seconds (0.0));

  FlowMonitorHelper flowmonHelper;
  Ptr<FlowMonitor> monitor = flowmonHelper.InstallAll ();

  // Bounded but generous: covers handshake retransmission under loss plus
  // the full voice phase.
  Simulator::Stop (Seconds (g_dur + 60.0));
  Simulator::Run ();

  double voiceDelayMs = 0.0;
  double voiceJitterMs = 0.0;
  double voiceLossPct = 0.0;

  Ptr<Ipv4FlowClassifier> classifier =
      DynamicCast<Ipv4FlowClassifier> (flowmonHelper.GetClassifier ());
  for (const auto &kv : monitor->GetFlowStats ())
    {
      Ipv4FlowClassifier::FiveTuple tuple = classifier->FindFlow (kv.first);
      if (tuple.destinationPort != voicePort)
        {
          continue;
        }
      const FlowMonitor::FlowStats &stats = kv.second;
      if (stats.rxPackets > 0)
        {
          voiceDelayMs = stats.delaySum.GetSeconds () * 1000.0 / stats.rxPackets;
        }
      if (stats.rxPackets > 1)
        {
          voiceJitterMs = stats.jitterSum.GetSeconds () * 1000.0 / (stats.rxPackets - 1);
        }
      if (stats.txPackets > 0)
        {
          voiceLossPct = 100.0 * (double) (stats.txPackets - stats.rxPackets) / stats.txPackets;
        }
      break;
    }

  Simulator::Destroy ();

  std::cout << std::fixed << std::setprecision (3) << g_scheme << "," << loss << "," << delayMs
            << "," << g_ttfbMs << "," << voiceDelayMs << "," << voiceJitterMs << ","
            << voiceLossPct << std::endl;

  return 0;
}
