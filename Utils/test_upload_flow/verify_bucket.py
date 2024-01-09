import argparse
import functools
import json
import os
from pathlib import Path
import sys
import tempfile
from zipfile import ZipFile
from packaging.version import Version

from Tests.Marketplace.marketplace_services import init_storage_client
from Tests.Marketplace.upload_packs import download_and_extract_index
from Tests.scripts.utils.log_util import install_logging
from Tests.scripts.utils import logging_wrapper as logging

# Info log Messages


class VerifyMessages(str):
    NEW_PACK = "verified the new pack {} in the index and that version {} zip exists for bucket {}"
    MODIFIED_PACK = "verified the packs new version is in the index and that all the new items are present in the pack"
    NEW_VERSION = ("verified the new pack's version exists in the index and that the release notes is parsed correctly "
                   "in the changelog")
    RN = "verified the content of the release notes is in the changelog under the right version"
    HIDDEN = "verified the pack does not exist in index"
    README = "verified the readme content is parsed correctly and that there was no version bump if only readme was modified"
    FAILED_PACK = "verified the commit hash is not updated in the pack metadata in the index.zip"
    MODIFIED_ITEM_PATH = "verified the path of the pack item is modified"
    DEPENDENCY = "verified the new dependency is in the pack metadata"
    NEW_IMAGE = "verified the new image was uploaded"
    HIDDEN_DEPENDENCY = "verified the hidden dependency pack not in metadata.json"
    PACK_NOT_IN_MARKETPLACE = ("verified the pack {} is NOT in the index and that version 1.0.0 zip DOES NOT "
                               "exists under the pack path in {} bucket")


# dev buckets
XSOAR_TESTING_BUCKET = 'marketplace-dist-dev'
XSOAR_SAAS_TESTING_BUCKET = 'marketplace-saas-dist-dev'
XSIAM_TESTING_BUCKET = 'marketplace-v2-dist-dev'

# Test pack names
TestUploadFlowXSOAR = 'TestUploadFlowXSOAR'
TestUploadFlowXsoarSaaS = 'TestUploadFlowXsoarSaaS'

BUCKET_LINK_PREFIX = "https://console.cloud.google.com/storage/browser/"


def read_json(path):
    with open(path) as file:
        return json.load(file)


def logger(func):
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):

        pack_id = args[0]
        logging.info(f"Starting validation - {func.__name__} for pack '{pack_id}'")
        try:
            result, pack_id, message = func(self, *args, **kwargs)
            self.is_valid = self.is_valid and result

            if not result:
                raise Exception(f"Failed when running validation - {func.__name__}")

            logging.info(f"[{pack_id}] Successfully {message}")

        except FileNotFoundError as e:
            logging.error(f"Failed to verify {func.__name__} for pack {pack_id} -\n{e}")
            self.is_valid = False

        except Exception as e:
            logging.error(f"Failed to verify {func.__name__} for pack {pack_id} -\n{e}")
    return wrapper


class GCP:
    def __init__(self, service_account, storage_bucket_name, storage_base_path):
        storage_client = init_storage_client(service_account)
        self.storage_bucket = storage_client.bucket(storage_bucket_name)
        self.storage_base_path = storage_base_path
        self.extracting_destination = tempfile.mkdtemp()
        self.index_path, _, _ = download_and_extract_index(self.storage_bucket, self.extracting_destination,
                                                           self.storage_base_path)

    def download_and_extract_pack(self, pack_id, pack_version):
        """
        Downloads and extracts the pack with version zip from the bucket
        """
        pack_path = os.path.join(self.storage_base_path, pack_id, pack_version, f"{pack_id}.zip")
        pack = self.storage_bucket.blob(pack_path)
        if pack.exists():
            download_pack_path = os.path.join(self.extracting_destination, f"{pack_id}.zip")
            pack.download_to_filename(download_pack_path)
            with ZipFile(download_pack_path, 'r') as pack_zip:
                pack_zip.extractall(os.path.join(self.extracting_destination, pack_id))
            return os.path.join(self.extracting_destination, pack_id)
        else:
            logging.warning(f'{pack_id} pack of version {pack_version} was not found in the bucket. {pack_path=}')
            return False

    def download_image(self, pack_id):
        """
        Downloads the pack image.
        """
        image_path = os.path.join(self.storage_base_path, pack_id, f"{pack_id}_image.png")
        image = self.storage_bucket.blob(image_path)
        if image.exists():
            download_image_path = os.path.join(self.extracting_destination, f"{pack_id}_image.png")
            image.download_to_filename(download_image_path)
            return download_image_path
        else:
            raise FileNotFoundError(f'Image of pack {pack_id} was not found in the bucket')

    def is_in_index(self, pack_id):
        pack_path = os.path.join(self.index_path, pack_id)
        return os.path.exists(pack_path)

    def get_changelog_rn_by_version(self, pack_id, version):
        """
        Returns the release notes of a pack from the changelog file
        """
        changelog_path = os.path.join(self.index_path, pack_id, 'changelog.json')
        changelog = read_json(changelog_path)
        return changelog.get(version, {}).get('releaseNotes', '')

    def get_pack_metadata(self, pack_id):
        """
        Returns the metadata.json of the latest pack version from the pack's zip
        """
        metadata_path = os.path.join(self.extracting_destination, 'index', pack_id, 'metadata.json')
        return read_json(metadata_path)

    def is_items_in_pack(self, item_file_paths: list, pack_id: str):
        """
        Check if an item is inside the pack.
        """
        not_exists = []
        for item_path in item_file_paths:
            if not os.path.exists(os.path.join(self.extracting_destination, pack_id, item_path)):
                not_exists.append(item_path)

        if not_exists:
            raise FileNotFoundError(f"The following files were not found in the bucket: '{not_exists}'")
        return True

    def get_index_json(self):
        """
        Returns the index.json file from the bucket
        """
        index_json_path = os.path.join(self.storage_base_path, 'index.json')
        index_json = self.storage_bucket.blob(index_json_path)
        if index_json.exists():
            download_index_path = os.path.join(self.extracting_destination, 'index.json')
            index_json.download_to_filename(download_index_path)
            return read_json(download_index_path)
        else:
            raise FileNotFoundError('index.json was not found in the bucket')

    def get_flow_commit_hash(self):
        """
        Returns the flow commit hash from the index.json file

        Returns:
            str: The last flow commit hash
        """
        index_json = self.get_index_json()
        return index_json.get('commit')

    def get_max_version(self, pack_id):
        """
        Returns the max version of a given pack
        """
        changelog = self.get_changelog(pack_id)
        return str(max([Version(key) for key in changelog]))

    def get_changelog(self, pack_id):
        """
        Returns the changelog file of a given pack from the index
        """
        changelog_path = os.path.join(self.index_path, pack_id, 'changelog.json')
        return read_json(changelog_path)

    def get_pack_readme(self, pack_id):
        """
        Returns the pack README file
        """
        item_path = os.path.join(self.extracting_destination, pack_id, 'README.md')
        with open(item_path) as f:
            return f.read()

    def check_pack_version_in_index_and_all_pack_items_exist(self, pack_id, pack_items, pack_version):
        version_exists = [self.is_in_index(pack_id), self.download_and_extract_pack(pack_id, pack_version)]
        items_exists = [self.is_items_in_pack(item_file_paths, pack_id) for item_file_paths
                        in pack_items.values()]
        return all(version_exists) and all(items_exists)


class BucketVerifier:
    def __init__(self, gcp: GCP, bucket_name, versions_dict, items_dict):
        self.gcp = gcp
        self.bucket_name = bucket_name
        self.versions = versions_dict
        self.items_dict = items_dict
        self.is_valid = True  # This will be modified in the @logger wrapper function

    @logger
    def verify_new_pack(self, pack_id, pack_items, marketplace, version, release_notes):
        """
        Verify the pack is in the index, verify version 1.0.0 zip exists under the pack's path
        """
        pack_components_as_expected = self.gcp.check_pack_version_in_index_and_all_pack_items_exist(pack_id, pack_items, version)
        rn_as_expected = release_notes in self.gcp.get_changelog_rn_by_version(pack_id, self.versions[pack_id])
        return (pack_components_as_expected and rn_as_expected,
                pack_id,
                VerifyMessages.NEW_PACK.format(pack_id, version, marketplace))

    @logger
    def verify_pack_not_in_marketplace(self, pack_id, marketplace):
        """
        Verify the new XSOAR pack is NOT in the index, verify version 1.0.0 zip DOES NOT exists under the pack's path
        """
        version_exists = [self.gcp.is_in_index(pack_id), self.gcp.download_and_extract_pack(pack_id, '1.0.0')]
        return (not any(version_exists),
                pack_id,
                VerifyMessages.PACK_NOT_IN_MARKETPLACE.format(pack_id, marketplace)
                )

    @logger
    def verify_modified_pack(self, pack_id, pack_items, expected_rn):
        """
        Verify the pack's new version is in the index, verify the new version zip exists under the pack's path,
        verify all the new items are present in the pack
        """
        pack_exists = self.gcp.download_and_extract_pack(pack_id, self.versions[pack_id])
        if not pack_exists:
            return False, pack_id
        changelog_as_expected = expected_rn in self.gcp.get_changelog_rn_by_version(pack_id, self.versions[pack_id])
        items_exists = [self.gcp.is_items_in_pack(item_file_paths, pack_id) for item_file_paths
                        in pack_items.values()]
        return changelog_as_expected and all(items_exists), pack_id, VerifyMessages.MODIFIED_PACK

    @logger
    def verify_new_version(self, pack_id, rn):
        """
        Verify a new version exists in the index, verify the rn is parsed correctly to the changelog
        """
        new_version_exists = self.gcp.download_and_extract_pack(pack_id, self.versions[pack_id])
        if not new_version_exists:
            return False, pack_id
        new_version_exists_in_changelog = rn in self.gcp.get_changelog_rn_by_version(pack_id, self.versions[pack_id])
        new_version_exists_in_metadata = self.gcp.get_pack_metadata(pack_id).get('currentVersion') == self.versions[pack_id]
        return (all
                ([new_version_exists, new_version_exists_in_changelog, new_version_exists_in_metadata]),
                pack_id,
                VerifyMessages.NEW_VERSION)

    @logger
    def verify_rn(self, pack_id, rn):
        """
        Verify the content of the RN is in the changelog under the right version
        """
        return rn in self.gcp.get_changelog_rn_by_version(pack_id, self.versions[pack_id]), pack_id, VerifyMessages.RN

    @logger
    def verify_hidden(self, pack_id):
        """
        Verify the pack does not exist in index
        """
        return not self.gcp.is_in_index(pack_id), pack_id, VerifyMessages.HIDDEN

    @logger
    def verify_readme(self, pack_id, readme):
        """
        Verify readme content is parsed correctly, verify that there was no version bump if only readme was modified
        """
        pack_exists = self.gcp.download_and_extract_pack(pack_id, self.versions[pack_id])
        if not pack_exists:
            return False, pack_id

        return self.gcp.get_max_version(pack_id) == self.versions[pack_id] and \
            readme in self.gcp.get_pack_readme(pack_id), pack_id, VerifyMessages.README

    @logger
    def verify_failed_pack(self, pack_id):
        """
        Verify commit hash is not updated in the pack's metadata in the index.zip
        """
        return (self.gcp.get_flow_commit_hash() != self.gcp.get_pack_metadata(pack_id).get('commit'),
                pack_id,
                VerifyMessages.FAILED_PACK
                )

    @logger
    def verify_modified_item_path(self, pack_id, modified_item_path, pack_items):
        """
        Verify the path of the item is modified
        """
        pack_exists = self.gcp.download_and_extract_pack(pack_id, self.versions[pack_id])
        if not pack_exists:
            return False, pack_id

        modified_item_exist = self.gcp.is_items_in_pack([modified_item_path], pack_id)
        items_exists = [self.gcp.is_items_in_pack(item_file_paths, pack_id) for item_file_paths
                        in pack_items.values()]
        return modified_item_exist and all(items_exists), pack_id, VerifyMessages.MODIFIED_ITEM_PATH

    @logger
    def verify_dependency(self, pack_id, dependency_id):
        """
        Verify the new dependency is in the metadata
        """
        # TODO: Should verify the dependency in the pack zip metadata as well - after CIAC-4686 is fixed
        return (dependency_id in self.gcp.get_pack_metadata(pack_id).get('dependencies', {}),
                pack_id,
                VerifyMessages.DEPENDENCY
                )

    @logger
    def verify_new_image(self, pack_id, new_image_path):
        """
        Verify the new image was uploaded
        """
        image_in_bucket_path = self.gcp.download_image(pack_id)
        return (open(image_in_bucket_path, "rb").read() == open(str(new_image_path), "rb").read(),
                pack_id,
                VerifyMessages.NEW_IMAGE
                )

    @logger
    def verify_hidden_dependency(self, pack_id, dependency_id):
        """
        Verify hidden dependency pack doesn't was added to the metadata
        """
        return (dependency_id not in self.gcp.get_pack_metadata(pack_id).get('dependencies', {}).keys(),
                pack_id,
                VerifyMessages.HIDDEN_DEPENDENCY
                )

    def run_xsiam_bucket_validations(self):
        """
        Runs the only XSIAM bucket verifications.
        - Checks both xsoar/xsoar_saas non related packs are not in the xsiam marketplace
        """
        self.verify_modified_item_path('AlibabaActionTrail', 'ModelingRules/modelingrule-Alibaba.yml',
                                       self.items_dict.get('AlibabaActionTrail'))
        packs_not_for_xsiam = [TestUploadFlowXSOAR, TestUploadFlowXsoarSaaS]
        for not_supported_xsiam_mp_pack in packs_not_for_xsiam:
            self.verify_pack_not_in_marketplace(not_supported_xsiam_mp_pack, XSIAM_TESTING_BUCKET)

    def run_xsoar_bucket_validations(self):
        """
        Runs the only XSOAR bucket verifications.
        - Checks that TestUploadFlowXSOAR xsoar bucket pack is in xsoar marketplace
        - Checks that TestUploadFlowXsoarSaaS xsoar_saas bucket pack is not in xsoar marketplace
        """
        self.verify_modified_item_path('CortexXDR', 'Scripts/script-XDRSyncScript_new_name.yml',
                                       self.items_dict.get('CortexXDR'))
        self.verify_pack_not_in_marketplace(TestUploadFlowXsoarSaaS, XSOAR_TESTING_BUCKET)
        expected_rn = """#### Integrations\n##### TestUploadFlow\nfirst release note xsoar"""
        self.verify_new_pack(TestUploadFlowXSOAR, self.items_dict.get(TestUploadFlowXSOAR),
                             XSOAR_TESTING_BUCKET, '1.0.0', expected_rn)

    def run_xsoar_saas_bucket_validation(self):
        """
        Runs the XSOAR SaaS bucket verifications.
        - Checks that TestUploadFlowXsoarSaaS xsoar bucket pack is in xsoar_saas marketplace
        """
        expected_rn = """#### Integrations\n##### TestUploadFlow\nfirst release note xsoar_saas"""
        self.verify_new_pack(TestUploadFlowXsoarSaaS, self.items_dict.get(TestUploadFlowXsoarSaaS),
                             XSOAR_SAAS_TESTING_BUCKET, '1.0.0', expected_rn)

    def run_validations(self):
        """
        Runs the basic verifications for both buckets.
        """
        expected_rn = """#### Integrations\n##### TestUploadFlow\nfirst release note"""
        # Case 1: Verify new pack - TestUploadFlow
        self.verify_new_pack('TestUploadFlow', self.items_dict.get('TestUploadFlow'), self.bucket_name, '1.0.0', expected_rn)

        # Case 2: Verify modified pack - Armorblox
        expected_rn = 'testing adding new RN'
        self.verify_modified_pack('Armorblox', self.items_dict.get('Armorblox'), expected_rn)

        # Case 3: Verify dependencies handling - Armis
        self.verify_dependency('Armis', 'TestUploadFlow')

        # Case 4: Verify new version - ZeroFox
        expected_rn = 'testing adding new RN'
        self.verify_new_version('ZeroFox', expected_rn)

        # Case 5: Verify modified existing release notes - Box
        expected_rn = 'testing modifying existing RN'
        self.verify_rn('Box', expected_rn)

        # Case 6: Verify pack is set to hidden - Microsoft365Defender
        self.verify_hidden('Microsoft365Defender')

        # Case 7: Verify changed readme - Maltiverse
        expected_readme = 'readme test upload flow'
        self.verify_readme('Maltiverse', expected_readme)

        # TODO: need to cause this pack to fail in another way because the current way cause validation to fail
        # Case 8: Verify failing pack - Absolute
        # self.verify_failed_pack('Absolute')

        # Case 9: Verify changed image - Armis
        self.verify_new_image('Armis', Path(
            __file__).parent / 'TestUploadFlow' / 'Integrations' / 'TestUploadFlow' / 'TestUploadFlow_image.png')

        # Case 12: Verify hidden dependency not in metadata.json
        self.verify_hidden_dependency('MicrosoftAdvancedThreatAnalytics', 'Microsoft365Defender')

        # Case 13+14: Verify that content items related only to specific mp exist only on the related mp.
        if self.bucket_name == XSIAM_TESTING_BUCKET:
            self.run_xsiam_bucket_validations()

        if self.bucket_name == XSOAR_TESTING_BUCKET:
            self.run_xsoar_bucket_validations()

        if self.bucket_name == XSOAR_SAAS_TESTING_BUCKET:
            self.run_xsoar_saas_bucket_validation()

    def is_bucket_valid(self):
        """
        Returns whether the bucket is valid.
        """
        logging.info(f"Bucket with name {self.bucket_name} is {'valid' if self.is_valid else 'invalid'}.\n"
                     f"See {BUCKET_LINK_PREFIX}{self.bucket_name}/{self.gcp.storage_base_path}")
        return self.is_valid


def validate_bucket(service_account, storage_base_path, bucket_name, versions_dict, items_dict):
    """
    Creates the GCP and BucketVerifier objects and runs the bucket validations.
    """
    gcp = GCP(service_account, bucket_name, storage_base_path)
    bucket_verifier = BucketVerifier(gcp, bucket_name, versions_dict, items_dict)
    bucket_verifier.run_validations()
    return bucket_verifier.is_bucket_valid()


def get_args():
    parser = argparse.ArgumentParser(description="Check if the created bucket is valid")
    parser.add_argument('-s', '--service-account', help="Path to gcloud service account", required=False)
    parser.add_argument('-sb', '--storage_base_path', help="Path to storage under the marketplace-dist-dev bucket",
                        required=False)
    parser.add_argument('-b', '--bucket-names', help="Storage bucket names as a comma separated value")
    parser.add_argument('-a', '--artifacts-path', help="path to artifacts from the script creating the test branch, "
                                                       "should contain json with dict of pack names and items to verify"
                                                       "and json with dict of pack names and versions to verify",
                        required=False)
    return parser.parse_args()


def main():
    install_logging('verify_bucket.log', logger=logging)

    args = get_args()
    storage_base_path = args.storage_base_path
    service_account = args.service_account
    storage_bucket_names = args.bucket_names
    versions_dict = read_json(os.path.join(args.artifacts_path, 'versions_dict.json'))
    items_dict = read_json(os.path.join(args.artifacts_path, 'packs_items.json'))

    storage_bucket_names_list = storage_bucket_names.split(',')

    are_buckets_valid = [validate_bucket(
        service_account=service_account,
        storage_base_path=storage_base_path,
        bucket_name=storage_bucket_name,
        versions_dict=versions_dict,
        items_dict=items_dict
    ) for storage_bucket_name in storage_bucket_names_list]

    if not all(are_buckets_valid):
        sys.exit(1)


if __name__ == "__main__":
    main()
    logging.success('All buckets are valid!')
