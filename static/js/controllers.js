'use strict';

/* Controllers */

var mediaControllers = angular.module('mediaControllers', []);

mediaControllers.controller('ToolbarCtrl', ['$scope', '$http', '$interval',
  function ($scope, $http, $interval) {
    $scope.bug_tags = [];
    $scope.ignore_bugs_with_status = [];
    $scope.status_options = ["Invalid", "Won't Fix", "New", "Triaged", "Fix Committed", "Fix Released", "Opinion", "In Progress"];
    $scope.ignore_bugs_with_priority = [];
    $scope.priority_options = ["Undecided", "Critical", "High", "Medium", "Low", "Wishlist"];
    $scope.filters = {};
    $scope.filters.milestones = [];
    $scope.mapping = {};
    $scope.mapping.milestones = {};

    $scope.socket = io();
    $scope.socket.on('update', function (msg) {
      $scope.update();
    });

    $scope.status_options.every(function(name, index){
      if(name == 'Fix Committed' || name == 'Fix Released'){
        $scope.ignore_bugs_with_status.push(false);
      } else {
        $scope.ignore_bugs_with_status.push(true);
      }
      return true;
    });

    $scope.priority_options.every(function(name, index){
      if(name == "Wishlist" || name == "Low"){
        $scope.ignore_bugs_with_priority.push(false);
      } else {
        $scope.ignore_bugs_with_priority.push(true);
      }
      return true;
    });

    $scope.update = function () {

      $http.get('/API/bugs').success(function (data) {
        $scope.bugs = data;
      });

      /*$http.get('/API/cards').success(function (data) {
        $scope.cards = data;
      });*/

      $http.get('/API/meta').success(function (data) {
        data.some(function (entry) {
          if (entry.url === 'https://api.launchpad.net/1.0/juju-core') {
            $scope.milestones = entry.milestones;
            $scope.milestones.push("none")
            $scope.milestones.every(function (milestone, index) {
              $scope.filters.milestones.push(true);
              $scope.mapping.milestones[milestone] = index;
              return true;
            });

            $scope.milestone_style = document.createElement('style');
            var width = 50 / $scope.milestones.length;
            $scope.milestone_style.innerHTML = ".milestone {width: " + width + "%;}";
            document.body.appendChild($scope.milestone_style);
          }
        })
      });
    };

    $scope.bug_filter = function (bug) {
      if (typeof $scope.milestones === 'undefined') {
        return false;
      }
      var activeMilestones = 0;
      $scope.milestones.every(function (milestone) {
        if ($scope.milestone_filter(milestone)) {
          activeMilestones++;
        }
        return true;
      });
      var width = 50 / activeMilestones;
      $scope.milestone_style.innerHTML = ".milestone {width: " + width + "%;}";

      var show = false;
      bug.tasks.some(function (task) {
        var show_task = $scope.task_filter(task);
        if (show_task) {
          show = true;
          return true;
        }
        return false;
      });
      return show;
    };

    $scope.task_filter = function (task) {
      if($scope.milestone_filter(task.milestone) == false){
          return false;
      }
      if(typeof task.status === 'undefined' &&
          typeof task.importance === 'undefined'){
        return false;
      }
      var show = true;
      if (typeof task.status !== 'undefined') {
        $scope.status_options.some(function (status, index) {
          if (task.status == status && !$scope.ignore_bugs_with_status[index]) {
            show = false;
            return true;
          }
          return false;
        });
      }

      if (typeof task.importance !== 'undefined') {
        if (show) {
          $scope.priority_options.some(function (priority, index) {
            if (task.importance == priority  && !$scope.ignore_bugs_with_priority[index]) {
              show = false;
              return true;
            }
            return false;
          });
        }
      }

      return show;
    };

    $scope.bugLabelImportance = function (bug) {
      switch (bug.importance) {
        case "Undecided":
          return "";
        case "Critical":
          return "label-danger";
        case "High":
          return "label-warning";
        case "Medium":
          return "label-success";
        case "Low":
          return "label-primary";
        case "Wishlist":
          return "label-info";
      }
      return "";
    };

    $scope.bugLabelStatus = function (bug) {
      switch (bug.status) {
        case "Invalid":
        case "Won't Fix":
          return "";
        case "New":
          return "label-danger";
        case "Triaged":
          return "label-warning";
        case "Fix Committed":
        case "Fix Released":
          return "label-success";
        case "Opinion":
          return "label-primary";
        case "In Progress":
          return "label-info";
      }
      return "";
    };

    $scope.rowClass = function (even) {
      if (even) {
        return 'row-even';
      } else {
        return 'row-odd';
      }
    };

    $scope.colClass = function (even) {
      if (even) {
        return 'col-even';
      } else {
        return 'col-odd';
      }
    };

    $scope.milestone_filter = function (milestone) {
      if (milestone === '') {
        milestone = 'none';
      }

      // Some milestones turn up in bug tasks that aren't blessed milestones.
      // We collect them in the none group.
      if (typeof $scope.mapping.milestones[milestone] === 'undefined') {
        milestone = 'none';
      }

      return $scope.filters.milestones[$scope.mapping.milestones[milestone]];
    };

    $scope.showTaskMilestone = function (task) {
      return $scope.milestone_filter(task.milestone);
    };

    $scope.showMilestoneSmall = function (task) {
      if ($scope.milestone_filter(task.milestone) == false) {
        return false;
      }
      return task.status || task.importance;
    };

    $scope.searchUser = "dooferlad";
    $scope.my_name = "James Tunnicliffe";

    $scope.myCardsFilter = function(card) {
      if(card.Board){
          return false;
      }

      var assigned_to_me = false;
      card.AssignedUsers.some(function(user){
        if(user.AssignedUserName == $scope.my_name){
          assigned_to_me = true;
          return true;
        }
        return false;
      });

      return assigned_to_me;
    }
  }]);
