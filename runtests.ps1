".\hashring.py",".\search.py",".\chord.py" | % {
    echo "Running test for $_"
    py -m doctest $_
    echo "Done"
}
