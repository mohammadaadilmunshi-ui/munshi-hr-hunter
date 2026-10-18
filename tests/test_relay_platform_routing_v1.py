"""Single generic external URL path, no SmartRecruiters-only fork."""
import pytest

from app.application_provider_ui_v1 import verified_apply_destination
from app.relay_application_dispatch_v1 import _prior_provider_application

SUPPORTED_EXAMPLES=[
    "https://company.wd1.myworkdayjobs.com/en-US/External/job/job123",
    "https://job-boards.greenhouse.io/company/jobs/123",
    "https://jobs.lever.co/company/123456789",
    "https://jobs.ashbyhq.com/company/role-123",
    "https://jobs.smartrecruiters.com/Company/744000123456789-title",
    "https://company.icims.com/jobs/1234/job",
    "https://apply.workable.com/company/j/ABCD/",
    "https://jobs.jobvite.com/company/job/abcd",
    "https://careers.dayforcehcm.com/en-US/company/CandidatePortal/jobs/1",
    "https://jobs.sap.com/job/sample-123",
    "https://company.bamboohr.com/careers/123",
]

@pytest.mark.parametrize("url",SUPPORTED_EXAMPLES)
def test_relay_does_not_assume_single_ats_domain(url):
    assert verified_apply_destination(
        {"source":"Indeed","apply_url":url,"job_url":url},
        connection=None,
    )==url


class ListingOnly:
    def __init__(self,url):
        self.url=url
        self.reads=0
    def call_read(self,tool,args):
        assert tool=="list_applications"
        self.reads+=1
        return {"structuredContent":{"result":{"applications":[]}}}

@pytest.mark.parametrize("url",SUPPORTED_EXAMPLES)
def test_cross_platform_duplicate_precheck_remains_read_only(url):
    provider=ListingOnly(url)
    assert _prior_provider_application(provider,url) is False
    assert provider.reads==1
