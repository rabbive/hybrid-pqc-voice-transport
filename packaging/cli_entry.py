import sys

from hpqv.demo import main

if __name__ == "__main__":
    if sys.argv[1:2] == ["selfcheck"]:
        import selfcheck
        sys.exit(selfcheck.main())
    main()
