"""CSV validation tests."""
from checker.csv_loader import parse_sites


def test_csv_validation_and_default_duration():
    """Valid rows load and invalid, duplicate, and unknown-region rows are reported."""
    data = "site_key,region,cron,max_duration_minutes\na,apac,0 */3 * * *,\na,apac,0 */3 * * *,120\nb,other,0 * * * *,120\nc,apac,nope,120\n"
    sites, errors = parse_sites(data, {"apac": "host"})
    assert len(sites) == 1 and sites[0].max_duration_minutes == 120
    assert len(errors) == 3
