#include "ns3/core-module.h"

using namespace ns3;

static void
PrintHello ()
{
  std::cout << "ns3 ok" << std::endl;
}

int
main (int argc, char *argv[])
{
  Simulator::Schedule (Seconds (0.0), &PrintHello);
  Simulator::Run ();
  Simulator::Destroy ();
  return 0;
}
