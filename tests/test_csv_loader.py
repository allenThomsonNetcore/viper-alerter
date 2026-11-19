"""CSV validation tests."""
from checker.csv_loader import describe_cron, parse_sites


def test_csv_validation_and_default_duration():
    """Valid rows load and invalid, duplicate, and unknown-region rows are reported."""
    data = "site_key,region,cron,max_duration_minutes,version,app\na,apac,0 */3 * * *,,v2,shopify\na,apac,0 */3 * * *,120,v1,bigcommerce\nb,other,0 * * * *,120,v1,shopify\nc,apac,nope,120,v1,shopify\n"
    sites, errors = parse_sites(data, {"apac": "host"})
    assert len(sites) == 1 and sites[0].max_duration_minutes == 120
    assert len(errors) == 3
    assert sites[0].version == "v2" and sites[0].app == "shopify"


def test_csv_rejects_unknown_app_or_version():
    """Only supported app and version values are accepted."""
    data = "site_key,region,cron,max_duration_minutes,version,app\na,apac,0 */3 * * *,120,v3,shopify\nb,apac,0 */3 * * *,120,v1,magento\n"
    sites, errors = parse_sites(data, {"apac": "host"})
    assert not sites
    assert len(errors) == 2


def test_cron_descriptions_are_readable_and_keep_nonuniform_minute_intervals_accurate():
    assert describe_cron("0 */3 * * *") == "Every 3 hours at minute 00 UTC"
    assert describe_cron("*/40 * * * *") == "At :00 and :40 every hour (the gaps alternate 40 and 20 minutes)"
    assert describe_cron("15 8 * * *") == "Daily at 08:15 UTC"
