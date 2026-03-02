from pyladdersim import Contact, InvertedContact, Ladder, OnDelayTimer, Output, Rung


def main():
    # Rung: Start AND (NOT Stop) -> TON timer -> Lamp coil
    start = Contact("Start")
    stop = InvertedContact("Stop")
    timer = OnDelayTimer("DelayTimer", PT=3)
    lamp = Output("Lamp")

    ladder = Ladder()
    ladder.add_rung(Rung([start, stop, timer, lamp]))

    print("Initial scan:", ladder.scan_once())
    print("Click contacts in the window to toggle Start/Stop.")
    print("Press Q in console or q in the window to stop.")

    ladder.run(visualize=True, cycle_time=0.5)


if __name__ == "__main__":
    main()
