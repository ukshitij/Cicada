".\communication.py",".\hashring.py",".\search.py",".\chord.py" | % {
    echo "Running test for $_"
    py -2 -m doctest $(Join-Path "chord" $_)
    echo "Done"
}
