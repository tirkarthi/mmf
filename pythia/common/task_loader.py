import os
import yaml

from torch.utils.data import DataLoader
from torch.utils.data.distributed import DistributedSampler

from pythia.tasks import MultiTask
from pythia.utils.distributed_utils import get_world_size
from .batch_collator import BatchCollator
from .test_reporter import TestReporter


class TaskLoader:
    def __init__(self, config):
        self.config = config

    def load_task(self):
        self.train_task = MultiTask('train', self.config)
        self.val_task = MultiTask('val', self.config)
        self.test_task = MultiTask('test', self.config)

        self.mapping = {
            'train': self.train_task,
            'val': self.val_task,
            'test': self.test_task
        }

        self.test_reporter = None
        self.should_not_log = self.config.training_parameters.should_not_log
        if self.config.training_parameters.evalai_predict is True:
            self.test_reporter = TestReporter(self.test_task)

    def get_config(self):
        return self.task_config

    def _load_task_config(self, task_name):
        directory = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(directory, '..', 'tasks',
                                   task_name, 'config.yml')
        task_config = {}
        if not os.path.exists(config_path):
            print("[Warning] No config present for task %s" %
                  task_name)
            return {}

        with open(config_path, 'r') as f:
            try:
                task_config = yaml.load(f)
            except yaml.YAMLError as err:
                print("[Error] Task %s's config yaml error" % self.task_name,
                      err)

        return task_config

    def make_dataloaders(self):
        training_parameters = self.config.training_parameters
        num_workers = training_parameters.num_workers
        pin_memory = training_parameters.pin_memory

        other_args = {}

        self._add_extra_args_for_dataloader(self.train_task, other_args)
        self.train_loader = DataLoader(dataset=self.train_task,
                                       pin_memory=pin_memory,
                                       collate_fn=BatchCollator(),
                                       num_workers=num_workers,
                                       **other_args)

        self.train_loader.dataset_type = 'train'

        self._add_extra_args_for_dataloader(self.val_task, other_args)
        self.val_loader = DataLoader(dataset=self.val_task,
                                     pin_memory=pin_memory,
                                     collate_fn=BatchCollator(),
                                     num_workers=num_workers,
                                     **other_args)
        self.val_loader.dataset_type = 'val'

        self._add_extra_args_for_dataloader(self.test_task, other_args)
        self.test_loader = DataLoader(dataset=self.test_task,
                                      pin_memory=pin_memory,
                                      collate_fn=BatchCollator(),
                                      num_workers=num_workers,
                                      **other_args)
        self.test_loader.dataset_type = 'test'

        self.use_cuda = "cuda" in self.config.training_parameters.device

    def _add_extra_args_for_dataloader(self, task, other_args={}):
        training_parameters = self.config.training_parameters

        if training_parameters.local_rank is not None \
            and training_parameters.distributed:
            other_args["sampler"] = DistributedSampler(task)
        else:
            other_args["shuffle"] = False
            if task.dataset_type != "test":
                other_args["shuffle"] = True

        batch_size = training_parameters.batch_size

        world_size = get_world_size()

        if batch_size % world_size != 0:
            raise RuntimeError("Batch size {} must be divisible by number "
                               "of GPUs {} used."
                               .format(batch_size, world_size))

        other_args["batch_size"] = batch_size // world_size

        return other_args

    def update_registry_for_model(self, config):
        self.train_task.update_registry_for_model(config)
        self.val_task.update_registry_for_model(config)
        self.test_task.update_registry_for_model(config)

    def clean_config(self, config):
        self.train_task.clean_config(config)
        self.val_task.clean_config(config)
        self.test_task.clean_config(config)

    def report_metrics(self, dataset_type, report, *args, **kwargs):
        if self.should_not_log:
            return
        # TODO: Complete this by calling child report metrics
        task = self.mapping[dataset_type]
        task.report_metrics(report, *args, **kwargs)

    def calculate_loss_and_metrics(self, report, *args, **kwargs):
        task = self.mapping[report.dataset_type]
        return task.calculate_loss_and_metrics(report, *args, **kwargs)

    def prepare_batch(self, batch, *args, **kwargs):
        return self.mapping[batch.dataset_type].prepare_batch(batch)

    def reset_meters(self, dataset_type):
        self.mapping[dataset_type].reset_meters()

    def verbose_dump(self, report, *args, **kwargs):
        if self.config.training_parameters.verbose_dump:
            dataset_type = report.dataset_type
            self.mapping[dataset_type].verbose_dump(report, *args, **kwargs)
